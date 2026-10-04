"""One version source: package.json. The lock file, the top CHANGELOG entry and ask.py --version all agree with it."""
import json
import re
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
ROOT = SKILL.parents[1]


def _version():
    return json.loads((ROOT / "package.json").read_text())["version"]


def test_lock_matches_package_json():
    lock = json.loads((ROOT / "package-lock.json").read_text())
    assert lock["version"] == _version()
    assert lock["packages"][""]["version"] == _version()


def test_top_changelog_entry_is_the_version():
    top = re.search(r"^## (\S+)", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert top and top.group(1) == _version()


def test_ask_version_prints_the_release_without_a_principal(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    out = subprocess.run([sys.executable, str(SKILL / "ask.py"), "--version"],
                         capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"Super Jev {_version()}"
