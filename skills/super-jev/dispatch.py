#!/usr/bin/env python3
"""One small dispatcher over the installed Super Jev tools; no judging logic."""
import json
import os
from pathlib import Path
import subprocess
import sys

TOOLS = {
    "help": "Quick Start for New Agents and focused setup answers",
    "skills": "Skills connector: find a skill",
    "find": "Brain/Documents/Repo connectors: find reviewed local evidence",
    "check": "Check a claim against evidence",
    "verify": "Verify an agent's work",
    "memory": "Use the opt-in experimental verified-reuse pointer/cache",
}


class MissingDependency(FileNotFoundError):
    """Raised when an optional dispatcher backend is unavailable."""


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
    elif tool == "memory":
        repo = Path(os.environ["SUPERJEV_REPO"]) if os.environ.get("SUPERJEV_REPO") else Path(__file__).resolve().parents[2]
        entry = repo / "experiments/verified-pointer-memory/cli.py"
        cmd = [sys.executable, str(entry), *args]
    else:
        entry = skill_dir / "superjev"
        if entry.is_file():
            cmd = ["bash", str(entry), "gate" if tool == "check" else "verify", *args]
        else:
            entry = skill_dir / "superjev.py"
            cmd = [sys.executable, str(entry), "gate" if tool == "check" else "verify", *args]
    if not entry.is_file():
        if tool == "memory":
            raise MissingDependency(str(entry))
        raise FileNotFoundError(f"Required Super Jev tool is not installed: {entry}")
    return cmd


def main(args: list[str]) -> int:
    if args and args[0] == "help":
        from setup_help import run as setup_help
        code, result = setup_help(args[1:])
        print(json.dumps(result))
        return code
    if not args or args[0] in ("tools", "--help", "-h"):
        print(json.dumps({"tools": TOOLS, "setupGuide": "references/connectors.md",
                          "setupWorkflows": {"execution": "agent-guided, not CLI commands",
                                             "options": ["connect existing data", "start a new collection",
                                                         "define a custom connector", "refresh reviewed data"]},
                          "controlPanel": "memory --describe"}))
        return 0
    tool, *rest = args
    if tool not in TOOLS:
        print(json.dumps({"status": "error", "reason": "Unknown tool; choose help, skills, find, check, verify, or memory"}))
        return 2
    try:
        return subprocess.run(command(Path(__file__).resolve().parent, tool, rest)).returncode
    except MissingDependency as exc:
        print(json.dumps({"status": "error", "reason": "missing-dependency", "dependency": str(exc)}))
        return 2
    except OSError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
