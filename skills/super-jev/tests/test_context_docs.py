#!/usr/bin/env python3
"""The docs say what the context setting does (context setting 6/7).

The setting is how much of each note one content check reads: Small, Medium or Large. A person picks it with
`--context NAME` on one ask, `SUPERJEV_CONTEXT` in the shell, or `--set-context NAME` saved for a principal; Small is
the default; and a read at a bigger size that fails is read again at Small, with a `CONTEXT:` line after the OUTCOME
line. The pages a person or an agent reads first have to say so. They say it with the numbers the judge profile states
(judge_profiles.json), so a change to the profile cannot leave a page behind, and they call the profile's own default
"the default".

  - SKILL.md, the daily page: the flag, the variable, the saved choice, what each size reads, the default, the CONTEXT
    line. The page is capped at 60 lines, so a sentence was reworded and no line added.
  - references/source-retrieval.md: the three read sizes, where the old page gave one fixed read for every note.
  - docs/jev-cli.md, the terminal app's page: the /context command. (Item 4 wrote it with the command; it is pinned here.)
  - CHANGELOG.md: the entry that introduced the setting, and one version in package.json, the lockfile and the changelog.

No Jev call, no network: only files of the repo are read.

    python3 -m pytest skills/super-jev/tests/test_context_docs.py -q
"""
import json
import re
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent
sys.path.insert(0, str(SKILL))
import context_setting  # noqa: E402
import judge_profile  # noqa: E402

DAILY = SKILL / "SKILL.md"
SOURCE_RETRIEVAL = SKILL / "references" / "source-retrieval.md"
CLI_DOC = REPO / "docs" / "jev-cli.md"
CHANGELOG = REPO / "CHANGELOG.md"
PACKAGE = REPO / "package.json"
LOCKFILE = REPO / "package-lock.json"

#: size name -> the judge profile's own row for it: what a page must say each size reads
SIZES = {name: judge_profile.load("typesafe-jev", context=name) for name in context_setting.NAMES}
DEFAULT = SIZES["small"].default_context


def _flat(text):
    """The text as one line of words, so a sentence wrapped across lines still reads as one."""
    return " ".join(text.split())


def _missing_sizes(flat):
    """The sizes a page does not give by name and by the number of characters the profile says each reads."""
    return [f"{name} ({size.read_chars:,} characters)" for name, size in SIZES.items()
            if f"{size.read_chars:,}" not in flat or not re.search(rf"\b{name}\b", flat, re.I)]


def _called_the_default(flat):
    """The sizes a page calls the default: a size's name, then within a few characters `the default` (`Small (the
    default)`, `small, the default,`, `Small is the default`)."""
    return {m.group(1).lower() for m in re.finditer(r"\b(small|medium|large)\b[^.;|]{0,12}\bthe default\b", flat, re.I)}


def _says_the_default(flat, where):
    assert _called_the_default(flat) == {DEFAULT}, (
        f"{where} must call the profile's default_context ({DEFAULT}) the default, once and only it: "
        f"found {sorted(_called_the_default(flat))}")


def test_skill_md_names_the_context_flag_and_stays_within_60_lines():
    text = DAILY.read_text()
    assert len(text.splitlines()) <= 60, f"SKILL.md has {len(text.splitlines())} lines; reword a line, never add one"
    flat = _flat(text)
    for needed in ("--context", "--set-context", context_setting.ENV):
        assert needed in flat, f"SKILL.md does not name {needed}"
    assert not _missing_sizes(flat), f"SKILL.md does not say what these read: {_missing_sizes(flat)}"
    _says_the_default(flat, "SKILL.md")


def test_source_retrieval_names_the_three_read_sizes():
    flat = _flat(SOURCE_RETRIEVAL.read_text())
    assert not _missing_sizes(flat), f"source-retrieval.md does not say what these read: {_missing_sizes(flat)}"
    _says_the_default(flat, "source-retrieval.md")
    # The page used to give one fixed read for every note (a note of about 12,000-14,000 characters has every
    # passage read, a longer one the best 14,000); a Medium or Large read is not that, so the sentence went.
    assert "12,000-14,000" not in flat, "source-retrieval.md still states the one fixed read of the old rule"


@pytest.mark.parametrize("page", [DAILY, SOURCE_RETRIEVAL], ids=["SKILL.md", "source-retrieval.md"])
def test_the_docs_say_a_failed_bigger_read_is_read_again_at_small(page):
    flat = _flat(page.read_text())
    assert "CONTEXT:" in flat, f"{page.name} does not show the CONTEXT line a fallback prints after the OUTCOME line"
    assert "read again at small" in flat.lower(), f"{page.name} does not say a failed Medium or Large read is read again at Small"


def test_cli_doc_lists_the_context_command():
    flat = _flat(CLI_DOC.read_text())
    for needed in ("/context [name]", "--context", "--set-context", context_setting.ENV):
        assert needed in flat, f"docs/jev-cli.md does not name {needed}"
    assert not _missing_sizes(flat), f"docs/jev-cli.md does not say what these read: {_missing_sizes(flat)}"
    _says_the_default(flat, "docs/jev-cli.md")


def _entries():
    """(heading, body as one line) for every entry of the changelog, newest first."""
    parts = re.split(r"^## ", CHANGELOG.read_text(), flags=re.M)[1:]
    return [(p.split("\n", 1)[0].strip(), _flat(p.split("\n", 1)[1])) for p in parts]


def test_changelog_top_entry_names_the_context_setting():
    """The top entry when it was written. It is found by what it says, not by where it stands, so the next release's
    entry does not break it: entries are added at the top, so the oldest one that names the setting's variable is the
    entry that introduced it. Its numbers are the sizes as that release shipped them (history, not today's profile)."""
    named = [(heading, body) for heading, body in _entries() if context_setting.ENV in body]
    assert named, f"no changelog entry names {context_setting.ENV}"
    heading, body = named[-1]
    assert re.fullmatch(r"\d+\.\d+\.\d+ . \d{4}-\d{2}-\d{2}", heading), f"the entry for the setting is not a release: {heading!r}"
    for needed in ("--context", "--set-context", context_setting.ENV, "CONTEXT:", "14,000", "56,000", "112,000"):
        assert needed in body, f"the {heading} entry does not name {needed}"
    assert "read again at small" in body.lower(), f"the {heading} entry does not say a failed bigger read is read again at Small"


def test_one_version_in_package_json_the_lockfile_and_the_newest_changelog_release():
    version = json.loads(PACKAGE.read_text())["version"]
    lock = json.loads(LOCKFILE.read_text())
    assert lock["version"] == version and lock["packages"][""]["version"] == version, (
        f"package-lock.json says {lock['version']} / {lock['packages']['']['version']}, package.json says {version}")
    newest = next(re.match(r"\d+\.\d+\.\d+", heading).group(0) for heading, _ in _entries() if re.match(r"\d+\.\d+\.\d+", heading))
    assert newest == version, f"package.json is {version}, the newest released changelog entry is {newest}"
