#!/usr/bin/env python3
"""Share already-connected pointers with more principals, at zero cost.

Usage:
  python3 share_pointers.py --principal AGENT [--principal AGENT2 ...] --pointer NAME [--pointer NAME2 ...] [--dry-run]
  python3 share_pointers.py --principal AGENT [--principal AGENT2 ...] --shared [--dry-run]

Adds each principal to a pointer's principals list with the memory runtime's `register` action
(same pointer, same dataset, current principals plus the new ones). Nothing is re-read by a
writer or re-gated by Jev: no provider call of any kind. `register` does start a new generation,
which drops that pointer's cached and pending answers; the count is printed before each share.
A pointer whose files changed since connect (preparation-required) cannot be shared until it is
refreshed. Pointer names are exact (no globs), and a pointer is refused (printed, skipped) when any
of its original sources sits under another agent's brain (~/agents/<bot>-brain/) or in a
documents/ or profile/ folder: only fleet-wide sets are shared.

--shared applies the fleet's shared list, <state dir>/shared-pointers.json (or $SUPERJEV_SHARED_POINTERS):
  {"pointers": ["fleet-knowledge", "main-skills-catalog", "main-skills-catalog-2"]}
prepare_bulk.py applies the same list to every principal it connects (see --no-shared there), so
each new principal starts with the fleet's shared knowledge and skills catalog, not only its own
brain. No file, or an empty list, shares nothing.
"""
import argparse, json, os, sqlite3, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from connect_checked import memory  # noqa: E402
from audit_visibility import _brain_owner  # noqa: E402

PRIVATE_PARTS = {"documents", "profile"}


def shared_config_path() -> Path:
    if os.environ.get("SUPERJEV_SHARED_POINTERS"):
        return Path(os.environ["SUPERJEV_SHARED_POINTERS"]).expanduser()
    root = os.environ.get("SUPERJEV_STATE_DIR")
    base = Path(root).expanduser() if root else Path.home() / ".local/state/super-jev"
    return base / "shared-pointers.json"


def load_shared(path: Path = None) -> list:
    """The shared pointer names (exact), or [] when there is no readable list."""
    path = path or shared_config_path()
    try:
        names = json.loads(path.read_text()).get("pointers") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [n for n in names if isinstance(n, str) and n.strip()]


def is_shared(pointer: str, names: list) -> bool:
    return pointer in names


def private_source(paths: list):
    """The first original source that makes a pointer private to one agent, with why, or None."""
    for path in paths:
        owner = _brain_owner(path)
        if owner:
            return f"{path} sits in {owner}'s brain"
        if PRIVATE_PARTS.intersection(x.casefold() for x in Path(path).parts):
            return f"{path} sits in a documents/ or profile/ folder"
    return None


def _rows(db: str) -> dict:
    """{pointer: body} read-only from the memory db (the panel hides other principals' pointers
    and never shows principals lists)."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {n: json.loads(b) for n, b in conn.execute("SELECT name, body FROM pointers")}
    finally:
        conn.close()


def _cached(db: str, pointer: str) -> int:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return sum(conn.execute(f"SELECT count(*) FROM {t} WHERE pointer=?", (pointer,)).fetchone()[0]
                   for t in ("cache", "pending"))
    finally:
        conn.close()


def share(names: list, principals: list, dry_run: bool = False, memory=memory) -> dict:
    """Add `principals` to each named pointer. Returns {pointer: outcome}, outcome one of
    "shared", "already", "would-share", "unknown-pointer", "refused: <why>" or "error: <reason>"."""
    if not names or not principals:
        return {}
    panel = memory({"action": "panel", "principal": principals[0]})
    paths = panel.get("storagePaths") or {}
    db = paths.get("db")
    if not db or not Path(db).is_file():
        return {n: "error: memory runtime not set up" for n in names}
    rows = _rows(db)
    try:
        datasets = json.loads(Path(paths.get("registry", "")).read_text()).get("datasets", {})
    except (OSError, ValueError):
        datasets = {}
    out = {}
    for name in names:
        if name not in rows:
            out[name] = "unknown-pointer"; continue
        body = rows[name]
        originals = [o.get("path", "") for o in (datasets.get(body.get("dataset")) or {}).get("originals") or []]
        why = private_source(originals) if originals else "no recorded source list to check"
        if why:
            out[name] = f"refused: {why}"
            print(f"share: refused {name}: {why}")
            continue
        have = body.get("principals") or []
        if set(principals) <= set(have):
            out[name] = "already"; continue
        if dry_run:
            out[name] = "would-share"; continue
        dropped = _cached(db, name)
        if dropped:
            print(f"share: {name}: new generation drops {dropped} cached/pending answer(s)")
        reg = memory({"action": "register", "pointer": name, "dataset": body["dataset"],
                      "principals": sorted(set(have) | set(principals))})
        out[name] = "shared" if reg.get("status") == "registered" else \
            f"error: {reg.get('reason') or reg.get('status') or 'register failed'}"
    return out


def share_defaults(principals: list, memory=memory, path: Path = None) -> dict:
    """Apply the fleet shared list to `principals` (prepare_bulk's onboarding default)."""
    names = load_shared(path)
    if not names:
        return {}
    out = share(names, principals, memory=memory)
    for name, outcome in out.items():
        if outcome != "already":
            print(f"shared: {name} -> {', '.join(principals)}: {outcome}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--principal", dest="principals", action="append", required=True)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--pointer", dest="pointers", action="append")
    group.add_argument("--shared", action="store_true", help=f"apply {shared_config_path()}")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    names = load_shared() if a.shared else a.pointers
    if not names:
        print(f"nothing to share: {shared_config_path()} lists no pointers"); return 1
    out = share(names, a.principals, a.dry_run)
    for name, outcome in out.items():
        print(f"{outcome:<16} {name}")
    return 1 if any(o.startswith(("error", "refused")) or o == "unknown-pointer" for o in out.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
