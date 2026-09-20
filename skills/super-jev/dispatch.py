#!/usr/bin/env python3
"""One small dispatcher over the installed Super Jev tools; no judging logic."""
import json
from pathlib import Path
import subprocess
import sys

TOOLS = {
    "skills": "Find a skill",
    "find": "Find information in reviewed brains or documents",
    "check": "Check a claim against evidence",
    "verify": "Verify an agent's work",
}


def command(skill_dir: Path, tool: str, args: list[str]) -> list[str]:
    root = skill_dir.parent
    if tool == "skills":
        entry = root / "skill-search/search.sh"
        extra = []
        if root.parent.name == ".claude" and "--config" not in args:
            extra = ["--config", str(root / "skill-search/roots-claude.json")]
        cmd = ["bash", str(entry), *args, *extra]
    elif tool == "find":
        entry = root / "fleet-retrieval-experiment/run.sh"
        cmd = ["sh", str(entry), *args]
    else:
        entry = skill_dir / "superjev"
        if entry.is_file():
            cmd = ["bash", str(entry), "gate" if tool == "check" else "verify", *args]
        else:
            entry = skill_dir / "superjev.py"
            cmd = [sys.executable, str(entry), "gate" if tool == "check" else "verify", *args]
    if not entry.is_file():
        raise FileNotFoundError(f"Required Super Jev tool is not installed: {entry}")
    return cmd


def main(args: list[str]) -> int:
    if not args or args[0] in ("tools", "--help", "-h"):
        print(json.dumps({"tools": TOOLS}))
        return 0
    tool, *rest = args
    if tool not in TOOLS:
        print(json.dumps({"status": "error", "reason": "Unknown tool; choose skills, find, check, or verify"}))
        return 2
    try:
        return subprocess.run(command(Path(__file__).resolve().parent, tool, rest)).returncode
    except OSError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
