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
prepare_bulk once by hand for those. Exit 1 if any refresh failed.
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
    if not report.get("principal") or not report.get("roots"):
        return None
    args = []
    for r in report["roots"]:
        args += ["--root", r]
    for e in report.get("excludes") or []:
        args += ["--exclude", e]
    if report.get("noRecurse"):
        args.append("--no-recurse")
    return args + ["--pointer", report["pointer"], "--principal", report["principal"], "--refresh"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pointer", action="append", default=[])
    ap.add_argument("--skip", action="append", default=[])
    ap.add_argument("--writer", choices=["auto", "claude", "builtin"])
    a = ap.parse_args(argv)
    failures = 0
    for rp in sorted(CACHE_DIR.glob("*-report.json")):
        try:
            report = json.loads(rp.read_text())
            name = report["pointer"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
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
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
