#!/usr/bin/env python3
"""Re-prepare only the pointers whose connected files changed.

A changed or deleted connected file stales its whole pointer (preparation-required) until
refreshed, and a file added inside a connected folder stays invisible until the pointer is
re-prepared. This walks every prepare-cache/<pointer>-report.json, compares each approved file's
sha256 with the cache, re-walks the recorded recipe for in-scope files the pointer has never seen
(for a legacy report pinned to its file list: new files directly in a folder that list connects),
and runs prepare_bulk.py --refresh with the pointer's recorded roots, principal, excludes and
--no-recurse ONLY for pointers with a change or a new file. Unchanged pointers are not touched,
so their approved answers survive (a reconnect rotates them).

Usage:
  python3 refresh_changed.py [--dry-run] [--pointer NAME ...] [--skip NAME ...] [--writer builtin]

Output opens with one outcome line: fresh | refreshed | stale (--dry-run) | needs-setup | error.
Exit code: 0 fresh/refreshed, 1 error, 2 needs-setup, 3 stale. A report that records no principal
(an agent's lookup heals it and records the asker; this command has no asker) is NEEDS-SETUP with the
one command to run, and so is a pointer with a prepare-cache/<pointer>.json but no report.
"""
import argparse, contextlib, fnmatch, hashlib, io, json, os, subprocess, sys, time
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


def known_across(cache_dir: Path = None, skip: str = None) -> set[str]:
    """known_files over every report in the cache (but `skip`'s own): a file another pointer
    already connects is never new to this one, and never moves into it."""
    known = set()
    for rp in sorted((cache_dir or CACHE_DIR).glob("*-report.json")):
        try:
            report = json.loads(rp.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(report, dict) and report.get("pointer") != skip:
            known |= known_files(report)
    return known


def pinned_list(report: dict) -> list | None:
    """The file list a legacy report is pinned to (prepare_bulk.replay_recipe), or None for a recipe."""
    if isinstance(report.get("scopeFiles"), list):
        return report["scopeFiles"]
    if "noRecurse" not in report:
        return [str(e[0] if isinstance(e, list) else e)
                for k in ("approved", "exceptions", "held") for e in report.get(k) or []]
    return None


def _maybe_new(entry, extensions: tuple, names: list, known: set) -> bool:
    """A file in a pinned folder that the inventory might admit and no report accounts for. Names
    the inventory always skips (dotfiles, *.bak*, logins, *-secret) never force a walk."""
    name = entry.name.casefold()
    stem = Path(name).stem
    if (not name.endswith(extensions) or name.startswith(".") or ".bak" in name or stem == "logins"
            or stem.endswith("-secret") or entry.path in known or os.path.realpath(entry.path) in known):
        return False
    if names and not any(fnmatch.fnmatch(name, n.lower()) for n in names):
        return False
    try:
        return entry.is_file() and entry.stat().st_size > 0
    except OSError:  # removed or renamed since the listing
        return False


def _entries(folder) -> list:
    """A folder's entries; an unreadable folder (permissions, macOS privacy) has none, never a crash."""
    try:
        with os.scandir(folder) as it:
            return list(it)
    except OSError:
        return []


def _reports(reports: list = None) -> list:
    if reports is not None:
        return reports
    out = []
    for rp in sorted(CACHE_DIR.glob("*-report.json")):
        try:
            out.append(json.loads(rp.read_text()))
        except (OSError, ValueError):
            continue
    return out


def _others(report: dict, reports: list) -> set:
    return set().union(*(known_files(r) for r in reports
                         if isinstance(r, dict) and r.get("pointer") != report.get("pointer")))


def new_files(report: dict, known: set, reports: list = None, snapshot: bool = True) -> list[str]:
    """In-scope files on disk that no pointer has seen: a note added to a connected folder after
    its connect (or a folder the old walk missed) stayed unfindable until someone reconnected by
    hand. Uses the recorded recipe. A legacy report pinned to its file list gains only what
    prepare_bulk.growth admits: files created after its growth snapshot (taken here the first time,
    unless `snapshot` is False) in a folder that held nothing outside the list then and that no
    other pointer shares. `known` spans every report, so a file another pointer already connects is
    not new; `reports` (every report, loaded from CACHE_DIR when not given) names the other
    pointers' files."""
    if not report.get("roots"):
        return []
    from prepare_bulk import (CONNECTABLE_EXTENSIONS, MAX_FILES, UNCONNECTED_TRIES, born_after, growth,
                              inventory, pinned_folders, read_snapshot, take_snapshot, vault_folder)
    # New files a refresh admitted but could not connect: the service never saw them, so nothing
    # else marks the pointer stale; they stay new for a few refreshes, until a connect succeeds.
    retry = [p for p in report.get("unconnectedNew") or [] if os.path.isfile(p)
             and (report.get("unconnectedTries") or 1) < UNCONNECTED_TRIES]
    extensions = tuple(CONNECTABLE_EXTENSIONS)
    # A vault folder (documents/, profile/ ...) never takes in new files on its own, so it is never
    # listed or walked for them: a root inside one (documents/<person>/medical, a recipe or a pinned
    # report alike) has nothing new here; a person refreshes it by hand.
    if any(vault_folder(r) for r in report["roots"]):
        return retry
    pinned = pinned_list(report)
    folders = pinned_folders(pinned) if pinned is not None else None
    if folders is not None:
        folders = {d for d in folders if not vault_folder(d)}
        if not folders:
            return retry
    snap = read_snapshot(report.get("pointer"), CACHE_DIR) if folders is not None else None
    if folders is not None and snap is not None:
        present = set(snap["present"])
        if not any(_maybe_new(e, extensions, report.get("names"), known) and e.path not in present
                   and born_after(e.path, snap["since"])
                   for d in folders for e in _entries(d)):
            return retry  # nothing created in a pinned folder since the snapshot: skip the walk
    elif folders is not None and not snapshot:
        return retry
    roots = [Path(r) for r in report["roots"] if Path(r).is_dir()]
    walked_at = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        files, held = inventory(roots, report.get("excludes"), report.get("noRecurse"),
                                report.get("names"), report.get("allowTargets"))
    found = [str(p) for p in files + [h[0] for h in held]]
    if folders is not None:
        others = _others(report, _reports(reports))
        if snap is None:  # first look: everything outside the list now waits for a person
            take_snapshot(report.get("pointer"), set(pinned), found, others, CACHE_DIR, since=walked_at,
                          roots=report["roots"])  # none while a root or pinned folder is missing
            return retry
        found = growth(set(pinned), found, snap, others)
        if len(found) > MAX_FILES:  # prepare_bulk would not add them
            return retry
    return retry + [p for p in found if p not in known and os.path.realpath(p) not in known and p not in retry]


def waiting(report: dict) -> list[str]:
    """Files a legacy pinned pointer's snapshot found outside its list that are still outside it."""
    from prepare_bulk import read_snapshot, vault_folder, waiting_files
    if any(vault_folder(r) for r in report.get("roots") or []):
        return []  # never grows on its own, so nothing waits on a person either
    pinned = pinned_list(report)
    snap = read_snapshot(report.get("pointer"), CACHE_DIR) if pinned is not None else None
    return waiting_files(snap, set(pinned)) if snap else []


def prepare_args(report: dict, asker: str = None) -> list[str] | None:
    # "principals" is the current field (every principal the pointer is registered for, so a
    # --refresh repeats them all and never narrows the pointer's scope); "principal" is the
    # older single-value field, still read for reports written before this field existed.
    # `asker` is the agent whose lookup sees this pointer (auto_heal gives it); it stands in only
    # when the report records no principal, and the refresh then records it. connect_part keeps
    # every principal the pointer is registered for.
    principals = report.get("principals") or ([report["principal"]] if report.get("principal") else [])
    principals = principals or ([asker] if asker else [])
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
    lines, stale, failed, setup = [], 0, 0, 0
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
        try:
            added = new_files(report, known, [r for r, _ in reports], snapshot=not a.dry_run)
        except OSError as e:  # one unreadable folder must not stop the other pointers' refreshes
            lines.append(f"NOTE  {name}: could not look for new files ({e.__class__.__name__}); changed files still count")
            added = []
        for w in waiting(report)[:1]:
            n = len(waiting(report))
            lines.append(f"WAITING {name}: {n} file(s) were in its folders before new-file pickup started and are "
                         f"not in it ({Path(w).name}{', ...' if n > 1 else ''}); check each, then run prepare_bulk.py "
                         f"--pointer {name} --principal AGENT --refresh --admit PATH (their folders take in new notes after)")
        if not changed and not added:
            continue
        args = prepare_args(report)
        if args is None:
            # No recorded principal and no asker to stand in: a person names one once, and the
            # refresh records it, so this pointer heals on its own after that.
            roots = " ".join(f"--root {r}" for r in report.get("roots") or [])
            lines.append(f"NEEDS-SETUP {name}: {len(changed)} changed, {len(added)} new, but its report records no "
                         f"principal. Run once: python3 {HERE / 'prepare_bulk.py'} {roots} --pointer {name} "
                         f"--principal AGENT --refresh (one --principal per agent it serves); after that it heals itself")
            setup += 1
            continue
        if a.writer:
            args += ["--writer", a.writer]
        what = changed or added
        lines.append(f"STALE {name}: {len(changed)} changed, {len(added)} new "
                     f"({Path(what[0]).name}{', ...' if len(what) > 1 else ''})")
        stale += 1
        if a.dry_run:
            continue
        r = subprocess.run([sys.executable, str(HERE / "prepare_bulk.py"), *args], cwd=HERE,
                           capture_output=True, text=True)
        lines.append(f"{'OK   ' if r.returncode in (0, 3) else 'FAIL '} {name}: prepare_bulk exit {r.returncode}")
        if r.returncode not in (0, 3):  # 3 = connected, some files held: refreshed
            failed += 1
            lines += [f"      {ln}" for ln in ((getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or "")).splitlines()[-5:]]

    # A pointer whose cache (prepare-cache/<pointer>.json) exists with no matching
    # <pointer>-report.json (a run that crashed after caching but before the report, or a
    # cache dropped in by hand) has no recorded roots/principal to refresh from. List it,
    # so it is not mistaken for "up to date".
    for cp in sorted(CACHE_DIR.glob("*.json")):
        name = cp.stem
        if name.endswith("-report") or name in reported:
            continue
        lines.append(f"NEEDS-SETUP {name}: prepare-cache/{name}.json exists with no {name}-report.json "
                     f"(no recorded roots/principal to refresh from); run prepare_bulk.py for it by hand")
        setup += 1
    # One outcome line first, and the exit code matches it: error 1, needs-setup 2, stale 3
    # (--dry-run only), else 0. A problem is never listed below a success line.
    if failed:
        code, head = 1, f"error: {failed} refresh(es) failed"
    elif setup:
        code, head = 2, f"needs-setup: {setup} pointer(s) cannot refresh themselves"
    elif a.dry_run and stale:
        code, head = 3, f"stale: {stale} pointer(s) would refresh"
    elif stale:
        code, head = 0, f"refreshed: {stale} pointer(s)"
    else:
        code, head = 0, "fresh: nothing changed"
    print("\n".join([head, *lines]))
    return code


if __name__ == "__main__":
    sys.exit(main())
