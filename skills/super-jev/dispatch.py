#!/usr/bin/env python3
"""One small dispatcher over the installed Super Jev tools; no judging logic."""
import json
import os
import shlex
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
    "audit-visibility": "Read-only: compare who can see a pointer against who connected it",
}


class MissingDependency(FileNotFoundError):
    """Raised when an optional dispatcher backend is unavailable."""


def config_path() -> Path:
    """The memory config setup.py writes (kept in step with setup.config_path)."""
    root = os.environ.get("SUPERJEV_STATE_DIR")
    base = Path(root).expanduser() if root else Path.home() / ".local/state/super-jev"
    return base / "_memory" / "config.json"


class NotSetUp(FileNotFoundError):
    """Raised when memory is used before setup.py has written its config."""


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
        wrapper = skill_dir / "memory.sh"
        # The installed wrapper supplies deployment-local repo/config, then re-enters
        # this dispatcher. Explicit repo/config choices must not be overwritten.
        if (not os.environ.get("SUPERJEV_REPO") and "--config" not in args
                and not any(arg.startswith("--config=") for arg in args)
                and not os.environ.get("SUPERJEV_MEMORY_WRAPPER_ACTIVE") and wrapper.is_file()):
            return ["sh", str(wrapper), *args]
        repo = Path(os.environ["SUPERJEV_REPO"]) if os.environ.get("SUPERJEV_REPO") else Path(__file__).resolve().parents[2]
        entry = repo / "experiments/verified-pointer-memory/cli.py"
        cmd = [sys.executable, str(entry), *args]
        # No explicit config and no wrapper: use the one setup.py writes.
        if ("--config" not in args and not any(arg.startswith("--config=") for arg in args)
                and "--describe" not in args):
            if not config_path().is_file():
                raise NotSetUp(str(config_path()))
            cmd += ["--config", str(config_path())]
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
    if args and args[0] == "audit-visibility":
        from audit_visibility import run as audit_visibility
        code, result = audit_visibility(args[1:])
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
        cmd = command(Path(__file__).resolve().parent, tool, rest)
        env = dict(os.environ)
        if tool == "memory" and len(cmd) > 1 and Path(cmd[1]).name == "memory.sh":
            env["SUPERJEV_MEMORY_WRAPPER_ACTIVE"] = "1"
        if tool == "find":
            result = subprocess.run(cmd, env=env, stdout=subprocess.PIPE)
            output = result.stdout
            try:
                body = json.loads(output)
            except (ValueError, UnicodeError):
                body = None
            if isinstance(body, dict) and body.get("status") == "preparation-required":
                body.setdefault("message", "Let's connect your records so Super Jev can search them. Choose the original files for this person or project; the agent can review them and run connect to build the searchable copy. If these files were connected before, refresh that connection instead.")
                body.setdefault("hint", "These records are not searchable yet, or their preparation is stale. Use the memory connect action with the original record paths for this person/project, review the returned file hashes under existing authorization, confirm reviewed:true, then retry the original question through memory using the returned pointer. See the connector setup example.")
                body.setdefault("nextAction", "connect-reviewed-local-files")
                body["connectCommand"] = shlex.join([sys.executable, str(Path(__file__).resolve()), "memory", "--connect", "my-records", "--file", "/absolute/path/to/record.md", "--principal", "YOUR_AGENT_NAME"])
                body["commandHint"] = "Replace the example file path and agent name. This command previews locally; review the files and permission, then run its returned confirmCommand. Repeat --file for more files."
                body["setup"] = {
                    "guide": "references/connectors.md#connect-local-files-paths-to-searchable-passages",
                    "command": "python3 <skill-directory>/dispatch.py memory --input CONNECT.json",
                    "requestTemplate": {"action": "connect", "pointer": "CHOSEN_SCOPE_NAME",
                                        "principals": ["YOUR_AGENT_NAME"],
                                        "sources": [{"path": "/absolute/path/to/original-record.md"}]},
                    "steps": [
                        "Select only the authorized person's/project's original text records, not a pointer-only index. Inspect existing memory pointers before choosing a name.",
                        "Submit connect for a local preview; review source text and provider permission, then copy returned sha256 values into sources and add reviewed:true.",
                        "For an existing connector, use its dataset and exact principal scope with replace:true only after review; otherwise use a separately named connector.",
                        "After registered, retry the original question through memory with the returned pointer. Do not rerun find against the old unprepared dataset.",
                    ],
                    "boundary": "Proceed within existing authorization; ask only for missing permission or unclear scope. Do not expand to other patients or send private text without authorization. If runtime/storage access is missing, report that specific blocker.",
                }
                body = {"message": body.pop("message"), **body}
                output = (json.dumps(body) + "\n").encode()
            sys.stdout.buffer.write(output)
            return result.returncode
        return subprocess.run(cmd, env=env).returncode
    except NotSetUp as exc:
        print(json.dumps({"status": "error", "reason": "not-set-up",
                          "message": "Super Jev is not set up yet. Run: python3 skills/super-jev/setup.py",
                          "missingConfig": str(exc)}))
        return 2
    except MissingDependency as exc:
        print(json.dumps({"status": "error", "reason": "missing-dependency", "dependency": str(exc),
                          "nextAction": "configure-memory-runtime",
                          "hint": "Memory needs the Super Jev repository runtime first. Set SUPERJEV_REPO to its checkout (containing experiments/verified-pointer-memory/cli.py), or repair the installed memory.sh wrapper. Then run memory --describe; data queries also need a reviewed dataset and memory config.",
                          "helpCommand": "help --topic register-setup"}))
        return 2
    except OSError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
