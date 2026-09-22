#!/usr/bin/env python3
"""One-command setup (and uninstall) for Super Jev.

    python3 skills/super-jev/setup.py              # set up; safe to run again
    python3 skills/super-jev/setup.py --uninstall  # remove everything setup and connect created

Setup creates the state folder ($SUPERJEV_STATE_DIR, default
~/.local/state/super-jev) with owner-only permissions and a memory config
inside it, checks Node, Python and the TypeSafe key, and prints the next
step. It never overwrites an existing config and never reads or prints the
key's value.

Uninstall removes the state folder (connected pointers, cached answers,
lookup logs, manual records), this skill's prepare-cache/ and ledger/
folders, and any ~/.claude/skills links that point into this checkout.
Your original files are never touched.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
REPO = SKILL_DIR.parent.parent
IN_REPO_LEFTOVERS = [SKILL_DIR / "prepare-cache", SKILL_DIR / "ledger"]


def state_root() -> Path:
    root = os.environ.get("SUPERJEV_STATE_DIR")
    return Path(root).expanduser() if root else Path.home() / ".local/state/super-jev"


def config_path() -> Path:
    return state_root() / "_memory" / "config.json"


def _node_major():
    try:
        out = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout
        return int(out.strip().lstrip("v").split(".")[0])
    except (OSError, ValueError):
        return None


def setup() -> int:
    problems = []
    node = _node_major()
    if node is None or node < 24:
        problems.append("Node 24 or newer is required (found: %s). Install it, then run setup again."
                        % ("none" if node is None else f"v{node}"))
    if sys.version_info < (3, 10):
        problems.append("Python 3.10 or newer is required.")

    cfg = config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    for d in (state_root(), cfg.parent):
        os.chmod(d, 0o700)
    if cfg.is_file():
        print(f"ok    memory config already exists: {cfg} (left unchanged)")
    else:
        cfg.write_text(json.dumps({"db": "memory.sqlite3", "registry": "registry.json"},
                                  indent=2) + "\n")
        os.chmod(cfg, 0o600)
        print(f"made  memory config: {cfg}")
    print(f"ok    state folder: {state_root()}")

    if os.environ.get("TYPESAFE_API_KEY", "").strip():
        print("ok    TYPESAFE_API_KEY is set")
    else:
        problems.append("TYPESAFE_API_KEY is not set. Every check and connect needs it. Run:\n"
                        "        export TYPESAFE_API_KEY=\"$(cat /path/to/your/typesafe-key-file)\"")

    if shutil.which("claude"):
        print("ok    description writer: claude CLI found (connect uses it only if it is logged in; "
              "--writer builtin needs no login)")
    else:
        print("ok    description writer: no claude CLI, so connect uses the built-in writer "
              "(no extra login needed)")

    if problems:
        print("\nNOT READY:")
        for p in problems:
            print("  - " + p)
        print("\nFix the above, then run setup again.")
        return 1
    print("\nREADY. Next step: connect a folder of .md files (AGENTS.md step 4):\n"
          "  python3 skills/super-jev/prepare_bulk.py --root /path/to/folder "
          "--pointer my-notes --principal me --writer builtin")
    return 0


def _links_into_repo():
    skills = Path.home() / ".claude" / "skills"
    if not skills.is_dir():
        return []
    out = []
    for p in skills.iterdir():
        if p.is_symlink():
            try:
                target = p.resolve()
            except OSError:
                continue
            if target == REPO or REPO in target.parents:
                out.append(p)
    return out


def uninstall() -> int:
    removed = []
    root = state_root()
    if root.exists() and not config_path().is_file():
        # Only a folder setup made (it holds setup's _memory/config.json) is ours to
        # delete; SUPERJEV_STATE_DIR may point at a folder of the user's own files.
        print(f"REFUSED: {root} has no {config_path().relative_to(root)} (the marker setup "
              "writes), so it may not be a Super Jev state folder. Nothing was deleted; "
              "check SUPERJEV_STATE_DIR, or delete that folder yourself if it is Super Jev's.")
        return 1
    for d in [root, *IN_REPO_LEFTOVERS]:
        if d.exists():
            shutil.rmtree(d)
            removed.append(str(d))
    for link in _links_into_repo():
        link.unlink()
        removed.append(str(link))
    # empty parents setup made (~/.local/state, ~/.local); never a folder with anything in it
    if not os.environ.get("SUPERJEV_STATE_DIR"):
        for parent in (Path.home() / ".local/state", Path.home() / ".local"):
            if not parent.is_dir() or any(parent.iterdir()):
                break
            parent.rmdir()
            removed.append(str(parent))
    for sub in ("skills", "experiments"):
        for cache in (REPO / sub).rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
    if removed:
        print("removed:\n  " + "\n  ".join(removed))
    print("Super Jev is uninstalled. Your original files were not touched. "
          "Delete this checkout folder to remove the code too.")
    return 0


def main(argv) -> int:
    if argv == ["--uninstall"]:
        return uninstall()
    if argv:
        print(__doc__)
        return 2
    return setup()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
