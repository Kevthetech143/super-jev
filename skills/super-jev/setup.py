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
# In-repo folders Super Jev writes to, with the file-name patterns it writes there.
# Uninstall deletes only matching files; anything else is left in place.
IN_REPO_LEFTOVERS = {SKILL_DIR / "prepare-cache": ("*.json", "*-held.txt"),
                     SKILL_DIR / "ledger": ("calls.jsonl",)}
# what ask.py writes under <state>/<principal>/
PRINCIPAL_FILES = {"lookups.jsonl", "manual"}


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
    root = state_root()
    if root.exists() and not cfg.is_file() and (not root.is_dir() or any(root.iterdir())):
        # A folder setup did not make, with something already in it, may be the user's own
        # files; planting a config there would later let --uninstall claim it.
        print(f"REFUSED: {root} already exists and is not empty, and setup did not make it. "
              "Point SUPERJEV_STATE_DIR at a new or empty folder, then run setup again.")
        return 1
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
    kept, repo_kept = [], []
    for d, patterns in IN_REPO_LEFTOVERS.items():
        if not d.is_dir() or d.is_symlink():
            continue
        for f in sorted(d.iterdir()):
            if f.is_file() and not f.is_symlink() and any(f.match(g) for g in patterns):
                f.unlink()
                removed.append(str(f))
            else:
                repo_kept.append(str(f))
        if not any(d.iterdir()):
            d.rmdir()
            removed.append(str(d))
    if root.is_dir():
        # Delete only what setup, connect and ask make: _memory/ and per-principal folders
        # holding nothing but lookups.jsonl and manual/. Anything else stays, and so does
        # the folder unless it is then empty.
        for child in sorted(root.iterdir()):
            ours = child.name == "_memory" or (
                child.is_dir() and not child.is_symlink()
                and all(c.name in PRINCIPAL_FILES for c in child.iterdir()))
            if ours:
                shutil.rmtree(child)
                removed.append(str(child))
            else:
                kept.append(str(child))
        if not kept:
            root.rmdir()
            removed.append(str(root))
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
    if repo_kept:
        print("left in place (not made by Super Jev):\n  " + "\n  ".join(repo_kept))
    if kept:
        print(f"left in place (not made by Super Jev), so {root} was kept:\n  " + "\n  ".join(kept))
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
