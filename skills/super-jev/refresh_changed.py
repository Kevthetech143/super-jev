#!/usr/bin/env python3
"""Re-prepare only the pointers whose connected files changed.

A changed or deleted connected file stales its whole pointer (preparation-required) until
refreshed, and a file added inside a connected folder stays invisible until the pointer is
re-prepared. This walks every prepare-cache/<pointer>-report.json, compares each approved file's
sha256 with the cache, re-walks the recorded recipe for in-scope files the pointer has never seen,
and runs prepare_bulk.py --refresh with the pointer's recorded roots, principal, excludes and
--no-recurse ONLY for pointers with a change or a new file. Unchanged pointers are not touched,
so their approved answers survive (a reconnect rotates them).

Usage:
  python3 refresh_changed.py [--dry-run] [--pointer NAME ...] [--skip NAME ...] [--writer builtin]

Reports written before prepare_bulk recorded its principal are skipped with a note; re-run
prepare_bulk once by hand for those. A pointer with a prepare-cache/<pointer>.json but no
matching <pointer>-report.json is listed as NEEDS MANUAL PREPARE, not skipped silently.
Exit 1 if any refresh failed.
"""
import argparse, contextlib, hashlib, io, json, os, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
CACHE_DIR = HERE / "prepare-cache"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def changed_files(report: dict, cache: dict) -> list[str]:
    """Approved files that are missing or whose bytes differ from the cached prepare."""
    out = []
    for f in report.get("approved", []):
        p, entry = Path(f), cache.get(f) or {}
        if not p.is_file() or sha(p) != entry.get("sha256"):
            out.append(f)
    return out


def known_files(report: dict) -> set[str]:
    """Every file a report already accounts for (connected, failed, held or removed)."""
    known = {str(e[0] if isinstance(e, list) else e)
             for k in ("approved", "exceptions", "held", "removed") for e in report.get(k) or []}
    return known | {os.path.realpath(k) for k in known}


def new_files(report: dict, known: set) -> list[str]:
    """In-scope files on disk that no pointer has seen: a note added to a connected folder after
    its connect (or a folder the old walk missed) stayed unfindable until someone reconnected by
    hand. Uses the recorded recipe; a legacy report pinned to its file list has no new files.
    `known` spans every report, so a file another pointer already connects is not new."""
    if not report.get("roots") or "noRecurse" not in report or isinstance(report.get("scopeFiles"), list):
        return []
    from prepare_bulk import inventory
    roots = [Path(r) for r in report["roots"] if Path(r).is_dir()]
    with contextlib.redirect_stdout(io.StringIO()):
        files, held = inventory(roots, report.get("excludes"), report.get("noRecurse"), False,
                                report.get("names"), report.get("allowTargets"))
    return [str(p) for p in files + [h[0] for h in held]
            if str(p) not in known and os.path.realpath(p) not in known]


def prepare_args(report: dict) -> list[str] | None:
    # "principals" is the current field (every principal the pointer is registered for, so a
    # --refresh repeats them all and never narrows the pointer's scope); "principal" is the
    # older single-value field, still read for reports written before this field existed.
    principals = report.get("principals") or ([report["principal"]] if report.get("principal") else [])
    if not principals or not report.get("roots"):
        return None
    args = []
    for r in report["roots"]:
        args += ["--root", r]
    for e in report.get("excludes") or []:
        args += ["--exclude", e]
    if report.get("noRecurse"):
        args.append("--no-recurse")
    for n in report.get("names") or []:
        args += ["--name", n]
    for t in report.get("allowTargets") or []:
        args += ["--allow-target", t]
    args += ["--pointer", report["pointer"]]
    for p in principals:
        args += ["--principal", p]
    return args + ["--refresh"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pointer", action="append", default=[])
    ap.add_argument("--skip", action="append", default=[])
    ap.add_argument("--writer", choices=["auto", "claude", "builtin"])
    a = ap.parse_args(argv)
    failures = 0
    reported = set()
    reports = []
    for rp in sorted(CACHE_DIR.glob("*-report.json")):
        try:
            report = json.loads(rp.read_text())
            reports.append((report, report["pointer"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    known = set().union(*(known_files(r) for r, _ in reports))
    for report, name in reports:
        reported.add(name)
        if (a.pointer and name not in a.pointer) or name in a.skip:
            continue
        cp = CACHE_DIR / f"{name}.json"
        cache = json.loads(cp.read_text()) if cp.is_file() else {}
        changed = changed_files(report, cache)
        added = new_files(report, known)
        if not changed and not added:
            continue
        args = prepare_args(report)
        if args is None:
            print(f"SKIP  {name}: {len(changed)} changed, but its report has no recorded principal; re-run prepare_bulk once by hand")
            continue
        if a.writer:
            args += ["--writer", a.writer]
        what = changed or added
        print(f"STALE {name}: {len(changed)} changed, {len(added)} new "
              f"({Path(what[0]).name}{', ...' if len(what) > 1 else ''})")
        if a.dry_run:
            continue
        r = subprocess.run([sys.executable, str(HERE / "prepare_bulk.py"), *args], cwd=HERE)
        print(f"{'OK   ' if r.returncode == 0 else 'FAIL '} {name}: prepare_bulk exit {r.returncode}")
        failures += r.returncode != 0

    # A pointer whose cache (prepare-cache/<pointer>.json) exists with no matching
    # <pointer>-report.json (a run that crashed after caching but before the report, or a
    # cache dropped in by hand) has no recorded roots/principal to refresh from -- it was
    # previously skipped in total silence. List it instead, so it is not mistaken for "up
    # to date".
    for cp in sorted(CACHE_DIR.glob("*.json")):
        name = cp.stem
        if name.endswith("-report") or name in reported:
            continue
        print(f"NEEDS MANUAL PREPARE  {name}: prepare-cache/{name}.json exists with no {name}-report.json "
              f"(no recorded roots/principal to refresh from); run prepare_bulk.py for it by hand")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
