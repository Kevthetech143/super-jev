"""Connect by sections: a Markdown file is split along its own headings into section files (file, heading path,
line range in each), a note over the size ceiling is split instead of held, a file with no headings or a small
one stays whole, line labels are written per section, and the original is never modified.

    python3 -m pytest skills/super-jev/tests/test_connect_sections.py -q
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("pb_sections", SKILL / "prepare_bulk.py")
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)
import sections  # noqa: E402
import refresh_changed as rc  # noqa: E402


def long_note() -> str:
    """A made-up garden log: headings at two levels, each section a few thousand characters."""
    out = ["# Garden log\n", "\n", "Kept by the allotment committee.\n", "\n"]
    for bed in ("Tomatoes", "Beans", "Squash"):
        out.append(f"## {bed}\n\n")
        for part in ("Planting", "Watering", "Harvest"):
            out.append(f"### {part}\n\n")
            out += [f"- {bed} {part.lower()} note {i}: bed {i % 7} row {i % 3} checked on 2026-05-{i % 28 + 1:02d}.\n"
                    for i in range(40)]
            out.append("\n")
    out.append("```\n# not a heading inside code\n```\n")
    return "".join(out)


def test_split_follows_the_files_own_headings():
    text = long_note()
    secs = sections.split(text)
    assert len(secs) == 9
    assert secs[0]["path"][:2] == ["Garden log", "Tomatoes"] and secs[0]["start"] == 1
    assert [s["path"][-1] for s in secs[:3]] == ["Planting", "Watering", "Harvest"]
    lines = text.splitlines(keepends=True)
    # the ranges cover the file exactly, in order, with each section's text verbatim
    assert secs[-1]["end"] == len(lines)
    for a, b in zip(secs, secs[1:]):
        assert b["start"] == a["end"] + 1
    for s in secs:
        assert s["text"] == "".join(lines[s["start"] - 1:s["end"]])
    assert not any("not a heading" in " ".join(s["path"]) for s in secs)


def test_small_file_and_file_with_no_headings_stay_whole():
    assert sections.split("# Note\n\nshort\n") == []
    assert sections.split("plain line of text\n" * 2000) == []  # 38 KB, no headings, under the ceiling


def test_file_over_the_ceiling_with_no_headings_is_cut_into_parts():
    text = ("word " * 15 + "\n\n") * 4000  # 328 KB, no headings
    secs = sections.split(text, ceiling=250_000)
    assert len(secs) > 1 and all(len(s["text"].encode()) <= 250_000 for s in secs)
    assert "".join(s["text"] for s in secs) == text
    assert secs[0]["path"] == ["part 1"]


def test_small_sections_join_a_neighbour():
    text = "# T\n\n## A\n" + "a line here\n" * 600 + "## B\nshort\n## C\n" + "c line here\n" * 600
    secs = sections.split(text)
    assert len(secs) == 2
    assert "B" in secs[0]["joined"] + secs[1]["joined"]


def test_write_sections_header_labels_and_original_untouched(tmp_path):
    src = tmp_path / "notes" / "garden.md"
    src.parent.mkdir()
    src.write_text(long_note())
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    parts = sections.write_sections(src, src.read_text(), tmp_path / "cache", "p")
    assert len(parts) == 9
    first, meta = parts[0]
    head = first.read_text().splitlines()
    assert head[0] == "# garden.md > Garden log > Tomatoes > Planting"
    assert head[1].startswith(f"(Section of {src}, lines 1-")
    assert sections.origin_of(first) == {"file": str(src), "heading": "Garden log > Tomatoes > Planting", "lines": meta["lines"]}
    assert sections.origin_lines(sections.origin_of(first), 4, 5) == (1, 2)
    lab = json.loads(first.with_suffix(".labels.json").read_text())
    assert lab["file"] == str(src) and lab["labels"]
    assert all(meta["lines"][0] <= r["line"] <= meta["lines"][1] for r in lab["labels"])
    assert any(r["dates"] for r in lab["labels"])
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    # a second run rewrites nothing; a shorter file removes the sections it no longer makes
    mtimes = {p: p.stat().st_mtime_ns for p, _ in parts}
    again = sections.write_sections(src, src.read_text(), tmp_path / "cache", "p")
    assert {p: p.stat().st_mtime_ns for p, _ in again} == mtimes
    assert sections.write_sections(src, "# small\n", tmp_path / "cache", "p") == []
    assert not sections.folder_for(tmp_path / "cache", "p", src).exists()


@pytest.fixture
def go(tmp_path, monkeypatch, capsys):
    OK = {"state": "SUPPORTED", "confidence": 0.95, "secs": 0}
    sent = []
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    monkeypatch.setattr(pb, "gate", lambda *a, **k: OK)
    monkeypatch.setattr(pb, "gate_many", lambda p, claims, *a, **k: [OK for _ in claims], raising=False)
    monkeypatch.setattr(pb, "connect_part", lambda ptr, pr, files, *a, **k: sent.extend(map(str, files)) or {"connected": True})

    def run(root, *extra):
        sent.clear()
        monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--root", str(root), "--pointer", "p", "--principal", "alice",
                                          "--no-shared", "--writer", "builtin", *extra])
        code = pb.main()
        return code, capsys.readouterr().out, list(sent)
    return run


def test_connect_sends_sections_and_records_the_originals(tmp_path, go):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "garden.md").write_text(long_note())
    (root / "plain.md").write_text("no headings at all, one short line\n")
    (root / "huge.md").write_text(("word " * 15 + "\n\n") * 4000)  # over 250,000 bytes: split, not held
    shas = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
    code, out, sent = go(root)
    assert code == 0, out
    assert "too big" not in out and "HELD 0" in out
    assert str(root / "plain.md") in sent
    assert str(root / "garden.md") not in sent and str(root / "huge.md") not in sent
    assert sum("/sections/p/garden-" in s for s in sent) == 9
    assert sum("/sections/p/huge-" in s for s in sent) > 1
    rep = json.loads((tmp_path / "cache" / "p-report.json").read_text())
    assert rep["sections"] is True and set(rep["sectioned"]) == {str(root / "garden.md"), str(root / "huge.md")}
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()} == shas
    # an edited original makes the pointer changed (its sections never change on their own), and it is known
    cache = json.loads((tmp_path / "cache" / "p.json").read_text())
    assert rc.changed_files(rep, cache) == []
    assert str(root / "garden.md") in rc.known_files(rep)
    with open(root / "garden.md", "a") as f:
        f.write("one more line\n")
    assert rc.changed_files(rep, cache) == [str(root / "garden.md")]


def test_no_sections_keeps_files_whole_but_still_splits_an_over_ceiling_note(tmp_path, go):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "garden.md").write_text(long_note())
    (root / "huge.md").write_text(("word " * 15 + "\n\n") * 4000)
    code, out, sent = go(root, "--no-sections")
    assert code == 0, out
    assert str(root / "garden.md") in sent and not any("/sections/p/garden-" in s for s in sent)
    assert any("/sections/p/huge-" in s for s in sent)
    rep = json.loads((tmp_path / "cache" / "p-report.json").read_text())
    assert rep["sections"] is False
