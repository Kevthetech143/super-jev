#!/usr/bin/env python3
"""Watched folders, the helper side. The rule itself lives in the engine (experiments/verified-pointer-memory,
Service.watched_refusal): a file whose real path lies under a watched pointer's folders may only be registered to a
pointer whose agents are a subset of the watched pointer's. Nothing here decides who may see a file at connect.

What this file holds: the report side of the mark ("watched": true, written first so even a cut-off report still
says it), the mark's own check of the reports on disk (a pointer inside the folders that records no agent, serves
agents the watched pointer does not, or has an unreadable report), and the real-path tests growth uses (a file joins
a watched pointer only if its real path lies inside the pointer's real roots).
"""
import json, os, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "prepare-cache"


def read_report_file(path) -> dict | None:
    """A report as a dict, or None when it is missing, torn or not a report."""
    try:
        rep = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return rep if isinstance(rep, dict) else None


def report_principals(rep: dict) -> list:
    return [x for x in (rep.get("principals") or ([rep["principal"]] if rep.get("principal") else []))
            if isinstance(x, str) and x]


def _real(path) -> str:
    return os.path.realpath(path).casefold()


def within(path, folder) -> bool:
    p, f = _real(path), _real(folder).rstrip(os.sep)
    return p == f or p.startswith(f + os.sep)


def _raw(path) -> tuple:
    """(roots read off the text or None, whether the text says watched: true) for a report that cannot be parsed."""
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return None, False
    roots = None
    m = re.search(r'"roots"\s*:\s*\[((?:\s*"(?:[^"\\]|\\.)*"\s*,?)*)\s*\]', text)
    if m:
        try:
            roots = [json.loads(x) for x in re.findall(r'"(?:[^"\\]|\\.)*"', m.group(1))]
        except ValueError:
            roots = None
    return roots, bool(re.search(r'"watched"\s*:\s*true', text))


def says_watched(path) -> bool:
    """Whether the text of a report that cannot be parsed says watched: true."""
    return _raw(path)[1]


def recovery(name: str, path) -> str:
    """The exact way to clear a report that cannot be read."""
    return (f"to clear it, rebuild {name} with: python3 prepare_bulk.py --root FOLDER --principal AGENT --pointer {name} "
            f"(its own folder and agents), or remove the file: rm {path}")


def reports(cache_dir: Path = None):
    """(name, report or None, path) for every report in the cache, in name order."""
    for rp in sorted(Path(cache_dir or CACHE_DIR).glob("*-report.json")):
        rep = read_report_file(rp)
        name = rp.name[:-len("-report.json")]
        yield (rep["pointer"] if rep and isinstance(rep.get("pointer"), str) else name), rep, rp


def mark_problems(pointer: str, rep: dict, cache_dir: Path = None) -> list:
    """[(name, why)] for each other pointer inside `rep`'s folders (by its recorded folders or files) whose report
    records no agent, serves an agent `rep` does not, or cannot be read (an unreadable one counts when its folders
    cannot be told). The engine checks the same on what it has registered; this reads the reports."""
    mine = set(report_principals(rep))
    roots = rep.get("roots") or []
    bad = []
    for name, other, path in reports(cache_dir):
        if name == pointer:
            continue
        if other is None:
            theirs, _ = _raw(path)
            if theirs is None or any(within(r, w) or within(w, r) for r in theirs for w in roots):
                bad.append((name, f"its report cannot be read; {recovery(name, path)}"))
            continue
        inside = any(within(r, w) for r in other.get("roots") or [] for w in roots) or any(
            within(f, w) for f in other.get("approved") or [] if isinstance(f, str) for w in roots)
        if not inside:
            continue
        theirs = set(report_principals(other))
        if not theirs:
            bad.append((name, "records no agent"))
        elif not theirs <= mine:
            bad.append((name, "serves agents this pointer does not"))
    return sorted(bad)


def inside_roots(path, roots) -> bool:
    """True when the real path of `path` lies inside the real path of one of `roots`."""
    return any(within(path, r) for r in roots)


def link_of(path, root) -> str:
    """The first symbolic link on `path` below `root` (as spelled under the root), else ''."""
    cur = Path(root)
    try:
        parts = Path(path).relative_to(root).parts
    except ValueError:
        return ""
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            return str(cur.relative_to(root))
    return ""


def split_out(paths, roots) -> tuple:
    """(inside, [link names]): `paths` whose real path lies inside the real `roots`, and the names of the links
    that lead the rest out. A name, never what is behind it."""
    keep, links = [], []
    for p in paths:
        if inside_roots(p, roots):
            keep.append(p)
            continue
        for r in roots:
            link = link_of(p, r)
            if link and link not in links:
                links.append(link)
    return keep, sorted(links)


def link_line(pointer: str, link: str) -> str:
    return (f"HELD  {pointer}: the link {link} leads outside the watched folder; the files behind it are not taken in "
            "(connect that folder as its own set)")


def save_report(pointer: str, report: dict, cache_dir: Path = None) -> Path:
    """Write a report in one step (a temp file, then os.replace) so a reader never sees half of it. A watched
    report carries its mark first, so a cut-off one still says it."""
    folder = Path(cache_dir or CACHE_DIR)
    target = folder / f"{pointer}-report.json"
    if report.get("watched") is True:
        report = {"watched": True, **{k: v for k, v in report.items() if k != "watched"}}
    tmp = folder / f".{target.name}.{os.getpid()}.tmp"
    try:
        tmp.write_text(json.dumps(report, indent=1))
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target
