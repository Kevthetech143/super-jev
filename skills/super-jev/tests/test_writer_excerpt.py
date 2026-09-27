#!/usr/bin/env python3
"""The writer's excerpt of a big file represents the whole file, in a bounded size, while a small
file's excerpt (and so builtin_writer's quote) is unchanged.

    python3 -m pytest skills/super-jev/tests/test_writer_excerpt.py -q
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "prepare_bulk.py"
spec = importlib.util.spec_from_file_location("prepare_bulk", SCRIPT)
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)


def old_excerpt(p):
    text = p.read_text(errors="replace")
    heads = [l.strip() for l in text.splitlines() if l.startswith("#")][:15]
    return {"path": str(p), "headings": heads, "start": text[:1200]}


def big_changelog(tmp_path, n_bullets=200, n_versions=30):
    lines = ["# Changelog", "", "## Unreleased"]
    lines += [f"- Change number {i}: topic-{i} was fixed in the widget {'x' * 120}" for i in range(n_bullets)]
    for v in range(n_versions, 0, -1):
        lines += [f"## 1.0.{v}", f"- release note for version {v}"]
    f = tmp_path / "CHANGELOG.md"
    f.write_text("\n".join(lines) + "\n")
    return f


def test_small_file_excerpt_is_unchanged(tmp_path):
    f = tmp_path / "small.md"
    f.write_text("# Title\n\n## A\nbody text here\n## B\nmore\n")
    assert pb.excerpt(f) == old_excerpt(f)


def test_big_file_samples_cover_the_whole_file(tmp_path):
    f = big_changelog(tmp_path)
    e = pb.excerpt(f)
    assert e["headings"][:15] == old_excerpt(f)["headings"]
    assert e["start"] == old_excerpt(f)["start"]
    text = f.read_text()
    assert len(e["samples"]) == pb.SAMPLES
    starts = [text.find(s) for s in e["samples"]]
    # Each passage starts on a line start, stays small, and together they span the file.
    for s, i in zip(e["samples"], starts):
        assert len(s) <= pb.SAMPLE_CHARS
        assert i > 1200 and text[i - 1] == "\n"
    assert starts == sorted(starts)
    assert starts[len(starts) // 2] > len(text) * 0.4
    assert starts[-1] > len(text) * 0.85


def test_headings_past_15_are_sampled_evenly_and_include_the_last(tmp_path):
    f = big_changelog(tmp_path, n_bullets=5, n_versions=60)
    heads = pb.excerpt(f)["headings"]
    assert len(heads) == pb.EXCERPT_HEADS + pb.EXCERPT_MORE_HEADS
    assert heads[-1] == "## 1.0.1"


def test_excerpt_size_is_bounded_whatever_the_file_size(tmp_path):
    small = len(json.dumps(pb.excerpt(big_changelog(tmp_path, 200, 40))))
    (tmp_path / "CHANGELOG.md").unlink()
    huge = len(json.dumps(pb.excerpt(big_changelog(tmp_path, 1500, 400))))
    assert huge < 7000 and abs(huge - small) < 1500


def test_builtin_writer_rebuilds_identically_from_old_and_new_excerpt(tmp_path):
    f = big_changelog(tmp_path)
    assert pb.builtin_writer([pb.excerpt(f)]) == pb.builtin_writer([old_excerpt(f)])


def test_secret_in_a_sample_is_caught_before_the_writer_runs(tmp_path, monkeypatch):
    f = big_changelog(tmp_path)
    text = f.read_text()
    # A secret on every line past the start, so some sample must carry one.
    head, tail = text[:1300], text[1300:]
    f.write_text(head + tail.replace("\n- Change", "\npassword = hunter2hunter2 - Change"))
    e = pb.excerpt(f)
    assert "hunter2" not in e["start"]
    assert any("hunter2" in s for s in e["samples"])
    called = []
    monkeypatch.setattr(pb.subprocess, "run", lambda *a, **k: called.append(1))
    with pytest.raises(pb.WriterError, match="secret"):
        pb.writer([e], "haiku")
    assert not called
