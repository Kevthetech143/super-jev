#!/usr/bin/env python3
"""The files a new user reads name no one's private machine.

A user-facing doc or skill file must not carry a real home path, a private agent folder, or a
fleet seat name: the reader does not have them, and a path that only exists on the author's
machine is a broken instruction. Patterns are generic; the only fleet-specific ones are the
primary-brain folder and the CLAW4MAC variable prefix.

    python3 -m pytest skills/super-jev/tests/test_no_private_names.py -q

Two files are left out on purpose, each for a stated reason (see EXCLUDED). Only files the repo
ships are read: in a git checkout a gitignored file (this machine's own skill-finder roots.json
and key provider) is never scanned, so a user's own setup cannot fail the suite.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

# Placeholders a doc may use for a home folder (same list as .github/gate.json placeholder_paths).
PLACEHOLDERS = r"(?:example|user|you)"
PATTERNS = {
    "a real home path (/Users/<name>/ or /home/<name>/)":
        re.compile(rf"/(?:Users|home)/(?!{PLACEHOLDERS}\b)[A-Za-z0-9_.-]+/"),
    "a private shared folder (~/agents/global or $HOME/agents/global)":
        re.compile(r"(?:~|\$HOME)/agents/global"),
    "a private brain folder (primary-brain)": re.compile(r"primary-brain"),
    "a fleet environment variable (CLAW4MAC)": re.compile(r"CLAW4MAC"),
}

SCOPE = [
    "README.md", "AGENTS.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md",
    "docs/*.md", "skills/*/SKILL.md", "skills/*/references/*.md", "skills/skill-search/**/*",
]

# Left out by name, each for a reason. Do not widen this list to make a failure go away.
EXCLUDED = {
    "skills/super-jev-build-cycle/": "a shipped optional skill (AGENTS.md) that still hard-codes a private contract path and an approver's name; held for a maintainer decision, not clean",
    "docs/KNOWN-QUIRKS.md": "quirk 11 honestly names the CLAW4MAC_* variables superjev.py still reads; scrubbing it would hide real behavior",
}


def _shipped(root):
    """Relative paths git would ship (tracked, or new and not ignored); None outside a git checkout."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return set(out.splitlines()) or None


def _in_scope(root=ROOT):
    seen = set()
    shipped = _shipped(root)
    for pattern in SCOPE:
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            if not path.is_file() or "node_modules" in rel or rel in seen:
                continue
            if shipped is not None and rel not in shipped:
                continue
            if any(rel == name or rel.startswith(name) for name in EXCLUDED):
                continue
            seen.add(rel)
            yield rel, path


def _hits(root=ROOT):
    out = []
    for rel, path in _in_scope(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # a binary file has no prose to read
        for number, line in enumerate(text.splitlines(), 1):
            for what, rx in PATTERNS.items():
                if rx.search(line):
                    out.append(f"{rel}:{number}: {what}: {line.strip()[:100]}")
    return out


def test_user_facing_files_name_no_private_machine():
    hits = _hits()
    assert not hits, "private names in user-facing files:\n" + "\n".join(hits)


def test_the_scan_really_covers_the_files_a_user_reads():
    seen = {rel for rel, _ in _in_scope()}
    for must in ("README.md", "AGENTS.md", "CHANGELOG.md", "skills/super-jev/SKILL.md",
                 "skills/super-jev/references/checking.md", "skills/skill-search/SKILL.md"):
        assert must in seen, f"{must} is not scanned"
    assert not any(rel.startswith("skills/super-jev-build-cycle/") or rel == "docs/KNOWN-QUIRKS.md" for rel in seen)
    for name in EXCLUDED:
        assert (ROOT / name).exists(), f"excluded name {name} no longer exists: drop it from EXCLUDED"


def test_the_patterns_catch_what_they_name_and_pass_placeholders():
    rx = PATTERNS
    real = ["see /Users/alice/notes.md", "cd /home/bob/work/", "~/agents/global/tools", "$HOME/agents/global/x",
            "ops/primary-brain/notes", "reads CLAW4MAC_BOT_ID"]
    for line in real:
        assert any(r.search(line) for r in rx.values()), line
    placeholders = ["/Users/example/notes.md", "/home/user/work/", "/Users/you/x/", "~/agents/<bot>-brain/",
                    "the ~/.claude/skills folder"]
    for line in placeholders:
        assert not any(r.search(line) for r in rx.values()), line


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_users_own_gitignored_setup_files_are_not_scanned(tmp_path):
    """A1 tells agents outside Claude Code to make roots.json with an absolute path (/Users/<name>/...).

    That file is gitignored, so it must not fail the suite; a shipped file with the same path must.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("skills/skill-search/roots.json\nskills/skill-search/deploy/local-key-provider.py\n")
    folder = tmp_path / "skills" / "skill-search"
    (folder / "deploy").mkdir(parents=True)
    (folder / "SKILL.md").write_text("Make roots.json and list your skill folders.\n")
    (folder / "roots.json").write_text('["/Users/alice/my-skills"]\n')
    (folder / "deploy" / "local-key-provider.py").write_text("KEY_FILE = '/Users/alice/.keys/typesafe'\n")
    assert _hits(tmp_path) == []
    assert {rel for rel, _ in _in_scope(tmp_path)} == {"skills/skill-search/SKILL.md"}

    # The same path in a file the repo ships (here a new, not-yet-added doc) is still caught.
    (folder / "roots.example.json").write_text('["/Users/alice/my-skills"]\n')
    hits = _hits(tmp_path)
    assert len(hits) == 1 and hits[0].startswith("skills/skill-search/roots.example.json:1:"), hits
