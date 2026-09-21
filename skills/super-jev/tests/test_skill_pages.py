#!/usr/bin/env python3
"""Sanity checks on the two shipped SKILL.md pages: valid frontmatter on both,
and the daily-use page kept short enough to actually be read.

    python3 -m pytest skills/super-jev/tests/test_skill_pages.py -q
"""
from pathlib import Path

SUPER_JEV_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = SUPER_JEV_DIR.parent
DAILY = SUPER_JEV_DIR / "SKILL.md"
CONNECT = SKILLS_DIR / "super-jev-connect" / "SKILL.md"


def _frontmatter(path: Path) -> dict:
    """Minimal frontmatter reader: no PyYAML dependency, just the simple
    `key: value` lines this repo's SKILL.md files actually use."""
    text = path.read_text()
    assert text.startswith("---\n"), f"{path} must start with a YAML frontmatter block"
    end = text.index("\n---", 4)
    block = text[4:end]
    fields = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


def test_daily_page_exists_with_valid_frontmatter():
    fields = _frontmatter(DAILY)
    assert fields.get("name") == "super-jev"
    assert fields.get("description")


def test_connect_page_exists_with_valid_frontmatter():
    fields = _frontmatter(CONNECT)
    assert fields.get("name") == "super-jev-connect"
    assert fields.get("description")


def test_daily_page_is_at_most_60_lines():
    lines = DAILY.read_text().splitlines()
    assert len(lines) <= 60, f"{DAILY} has {len(lines)} lines, want at most 60"


def test_daily_page_links_to_connect_skill_for_setup():
    text = DAILY.read_text()
    assert "super-jev-connect/SKILL.md" in text


def test_connect_page_links_back_to_daily_skill():
    text = CONNECT.read_text()
    assert "super-jev/SKILL.md" in text
