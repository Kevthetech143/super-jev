"""Read-only pointer-visibility audit.

For each principal, compares the pointers it can currently SEE (the
verified-pointer-memory sqlite `pointers` table, keyed by the
`principals` visibility list a `memory` register call sets) against the
pointers it actually CONNECTED (the path-connect `registry.json`
`pathConnection.principals` list recorded at connect time).

A pointer is flagged when:
  - it is visible to a principal that is not in its connect-time
    principals list ("visible-but-not-connected"); or
  - one of its original source paths resolves under another bot's own
    brain directory, `~/agents/<otherbot>-brain`, for a principal that
    is not that bot ("foreign-brain-root").

No writes: this module only reads a registry.json and a
verified-pointer-memory sqlite database and returns findings.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any


def _load_registry(registry_path: Path) -> dict[str, Any]:
    if not registry_path.is_file():
        return {"version": 1, "datasets": {}}
    return json.loads(registry_path.read_text())


def _load_pointers(db_path: Path) -> dict[str, dict[str, Any]]:
    if not Path(db_path).is_file():
        return {}
    pointers: dict[str, dict[str, Any]] = {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        try:
            rows = conn.execute("SELECT name, body FROM pointers").fetchall()
        except sqlite3.OperationalError:
            return {}
        for name, body in rows:
            try:
                pointers[name] = json.loads(body)
            except (ValueError, TypeError):
                continue
    finally:
        conn.close()
    return pointers


_BRAIN_RE = re.compile(r"/agents/([^/]+)-brain(?:/|$)")


def _brain_owner(path: str) -> str | None:
    """Return the bot name a path's root sits under, if any (`~/agents/<bot>-brain/...`)."""
    match = _BRAIN_RE.search(path)
    return match.group(1) if match else None


def audit(registry_path: Path, db_path: Path) -> dict[str, Any]:
    """Return {"principals": {name: {"visible": [...], "connected": [...],
    "flags": [...]}}} for every principal seen in either source."""
    registry = _load_registry(registry_path)
    datasets = registry.get("datasets", {})
    pointers = _load_pointers(db_path)

    # dataset name -> connect-time principals + original source paths
    connected_by_dataset: dict[str, list[str]] = {}
    originals_by_dataset: dict[str, list[str]] = {}
    for dataset_name, entry in datasets.items():
        path_connection = entry.get("pathConnection") or {}
        connected_by_dataset[dataset_name] = list(path_connection.get("principals") or [])
        originals_by_dataset[dataset_name] = [
            o.get("path", "") for o in entry.get("originals") or [] if isinstance(o, dict)
        ]

    principals: dict[str, dict[str, Any]] = {}

    def bucket(name: str) -> dict[str, Any]:
        return principals.setdefault(
            name, {"visible": [], "connected": [], "flags": []}
        )

    for pointer_name, body in pointers.items():
        dataset = body.get("dataset", "")
        visible_to = list(body.get("principals") or [])
        connected_principals = connected_by_dataset.get(dataset, [])
        originals = originals_by_dataset.get(dataset, [])

        for principal in visible_to:
            entry = bucket(principal)
            entry["visible"].append(pointer_name)

            if principal not in connected_principals:
                entry["flags"].append({
                    "pointer": pointer_name,
                    "dataset": dataset,
                    "type": "visible-but-not-connected",
                    "detail": (
                        f"{principal} can see pointer '{pointer_name}' (dataset "
                        f"'{dataset}') but did not connect it; connected by "
                        f"{connected_principals or 'nobody on record'}."
                    ),
                })

            for path in originals:
                owner = _brain_owner(path)
                if owner and owner != principal:
                    entry["flags"].append({
                        "pointer": pointer_name,
                        "dataset": dataset,
                        "type": "foreign-brain-root",
                        "detail": (
                            f"{principal} can see pointer '{pointer_name}' (dataset "
                            f"'{dataset}') whose source root sits inside "
                            f"{owner}'s brain ({path})."
                        ),
                    })

    for dataset, connected_principals in connected_by_dataset.items():
        for principal in connected_principals:
            bucket(principal)["connected"].append(dataset)

    return {"principals": principals}


def _resolve_from_config(config_path: Path) -> tuple[Path, Path]:
    """Read a pointer-memory config.json ({"db": ..., "registry": ...}) and
    resolve its db/registry the same way the live service does (cli.py):
    relative paths are relative to the config file's own directory. This is
    the real, working pointer list any principal's `ask.py` actually reads
    (e.g. /Users/admin/super-jev/.local/pointer-memory/config.json) -- not
    the ~/.local/state/super-jev/_memory test-fixture store, which is a
    different, unrelated config and should never be used for a live audit."""
    location = config_path.resolve()
    config = json.loads(location.read_text())
    base = location.parent

    def resolved(name: str) -> Path:
        p = Path(config[name])
        return p if p.is_absolute() else (base / p)

    return resolved("registry"), resolved("db")


def run(args: list[str]) -> tuple[int, dict[str, Any]]:
    """CLI entry: --config PATH, or --registry PATH --db PATH, [--principal NAME].

    --config points at a pointer-memory config.json (the same file
    memory.sh/ask.py resolve their registry+db from); its relative paths
    are resolved the same way the live service resolves them. --registry/
    --db remain for pointing at exact files directly (e.g. tests)."""
    registry_path = None
    db_path = None
    config_path = None
    only_principal = None
    it = iter(args)
    for arg in it:
        if arg == "--registry":
            registry_path = Path(next(it))
        elif arg == "--db":
            db_path = Path(next(it))
        elif arg == "--config":
            config_path = Path(next(it))
        elif arg == "--principal":
            only_principal = next(it)
        elif arg.startswith("--registry="):
            registry_path = Path(arg.split("=", 1)[1])
        elif arg.startswith("--db="):
            db_path = Path(arg.split("=", 1)[1])
        elif arg.startswith("--config="):
            config_path = Path(arg.split("=", 1)[1])
        elif arg.startswith("--principal="):
            only_principal = arg.split("=", 1)[1]

    if config_path is not None and (registry_path is None or db_path is None):
        try:
            cfg_registry, cfg_db = _resolve_from_config(config_path)
        except (OSError, ValueError, KeyError) as exc:
            return 2, {
                "status": "error",
                "reason": f"could not read --config {config_path}: {exc}",
            }
        registry_path = registry_path or cfg_registry
        db_path = db_path or cfg_db

    if registry_path is None or db_path is None:
        return 2, {
            "status": "error",
            "reason": (
                "audit-visibility requires --config PATH (a pointer-memory "
                "config.json, e.g. the one memory.sh/ask.py use) or explicit "
                "--registry PATH --db PATH"
            ),
        }

    result = audit(registry_path, db_path)
    if only_principal is not None:
        result = {
            "principals": {
                only_principal: result["principals"].get(
                    only_principal, {"visible": [], "connected": [], "flags": []}
                )
            }
        }

    any_flags = any(p["flags"] for p in result["principals"].values())
    return 0, {"status": "flagged" if any_flags else "clean", **result}
