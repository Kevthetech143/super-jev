#!/usr/bin/env python3
"""Re-prepare only the pointers whose connected files changed.

A changed or deleted connected file stales its whole pointer (preparation-required) until
refreshed. This walks every prepare-cache/<pointer>-report.json, compares each approved
file's sha256 with the cache, and runs prepare_bulk.py --refresh with the pointer's recorded
roots, principal, excludes and --no-recurse ONLY for pointers with a change. Unchanged
pointers are not touched, so their approved answers survive (a reconnect rotates them).

Usage:
  python3 refresh_changed.py [--dry-run] [--pointer NAME ...] [--skip NAME ...] [--writer builtin]

Reports written before prepare_bulk recorded its principal are skipped with a note; re-run
prepare_bulk once by hand for those. A pointer with a prepare-cache/<pointer>.json but no
matching <pointer>-report.json is listed as NEEDS MANUAL PREPARE, not skipped silently.
Exit 1 if any refresh failed.
"""
import argparse, hashlib, json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
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
    for rp in sorted(CACHE_DIR.glob("*-report.json")):
        try:
            report = json.loads(rp.read_text())
            name = report["pointer"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        reported.add(name)
        if (a.pointer and name not in a.pointer) or name in a.skip:
            continue
        cp = CACHE_DIR / f"{name}.json"
        cache = json.loads(cp.read_text()) if cp.is_file() else {}
        changed = changed_files(report, cache)
        if not changed:
            continue
        args = prepare_args(report)
        if args is None:
            print(f"SKIP  {name}: {len(changed)} changed, but its report has no recorded principal; re-run prepare_bulk once by hand")
            continue
        if a.writer:
            args += ["--writer", a.writer]
        print(f"STALE {name}: {len(changed)} changed ({Path(changed[0]).name}{', ...' if len(changed) > 1 else ''})")
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
