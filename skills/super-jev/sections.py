"""Connect by sections: split a Markdown file along its own headings into searchable section files.

Structure only (ATX headings outside code fences, blank lines), no topic words. The original file is
only read: each section is written as a derived file under prepare-cache/sections/<pointer>/, opening with
two lines that name its heading path, source file and line range, then the section's original lines
verbatim. Line labels (labels.py) are made per section at the same time, in a sidecar
<section>.labels.json, numbered by the ORIGINAL file's lines.

A file at or under TARGET_CHARS, or one with no headings, stays whole (unchanged behavior), unless it is
over the size ceiling: then it is cut at blank lines so it connects instead of being held.
"""
import hashlib, json, re
from pathlib import Path

import labels

TARGET_CHARS = 6_000   # a section over this splits at its next heading level
MIN_CHARS = 600        # a smaller piece joins its neighbour (a heading with one line is not a unit)
HEAD_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
HEADER_TAG = "(Section of "


def headings(lines: list) -> list:
    """[(index, level, title)] of ATX headings outside fenced code and front matter."""
    out, fence, start = [], None, 0
    if lines and lines[0].strip() == "---":
        end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() in ("---", "...")), None)
        start = end + 1 if end else 0
    for i in range(start, len(lines)):
        m = FENCE_RE.match(lines[i])
        if m:
            mark = m.group(1)[0]
            fence = None if fence == mark else (fence or mark)
            continue
        if fence:
            continue
        h = HEAD_RE.match(lines[i].rstrip("\r\n"))
        if h and h.group(2):
            out.append((i, len(h.group(1)), h.group(2).strip()))
    return out


def _size(lines, lo, hi):
    return sum(len(l) for l in lines[lo:hi])


def _chunk(lines, lo, hi, path, target):
    """Cut [lo, hi) at blank lines into pieces of about `target` chars (a line is never cut)."""
    out, start, size = [], lo, 0
    for i in range(lo, hi):
        size += len(lines[i])
        if size >= target and (not lines[i].strip() or size >= 2 * target) and i + 1 < hi:
            out.append([start, i + 1, path, []]); start, size = i + 1, 0
    out.append([start, hi, path, []])
    if len(out) > 1:
        for k, piece in enumerate(out, 1):
            piece[2] = path + [f"part {k}"]
    return out


def _pieces(lines, heads, lo, hi, path, target, ceiling):
    if _size(lines, lo, hi) <= target:
        return [[lo, hi, path, []]]
    # a range opens on its own heading (already in `path`); the file's first line may be a heading too
    inner = [h for h in heads if lo < h[0] < hi or (h[0] == lo and not path)]
    if not inner:
        return _chunk(lines, lo, hi, path, target) if _size(lines, lo, hi) > ceiling else [[lo, hi, path, []]]
    level = min(h[1] for h in inner)
    cuts = {h[0]: h[2] for h in inner if h[1] == level}
    bounds = [lo] + sorted(cuts) + [hi]
    out = []
    for a, b in zip(bounds, bounds[1:]):
        if a < b:
            out += _pieces(lines, heads, a, b, path + [cuts[a]] if a in cuts else path, target, ceiling)
    return out


def _common(a, b):
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return a[:n]


def _merge_small(pieces, lines, min_chars):
    """A piece under min_chars joins the piece before it (the first joins the next); the joined unit keeps
    the shared heading path and lists the joined headings."""
    glued, start = [], None
    for i, p in enumerate(pieces):  # a parent's short intro (heading and a line or two) opens its first child
        p = [start if start is not None else p[0], p[1], list(p[2]), list(p[3])]
        start = None
        nxt = pieces[i + 1] if i + 1 < len(pieces) else None
        if (nxt is not None and _size(lines, p[0], p[1]) < min_chars and len(nxt[2]) > len(p[2])
                and nxt[2][:len(p[2])] == p[2]):
            start = p[0]
            continue
        glued.append(p)
    out = []
    for p in glued:
        if out and (_size(lines, p[0], p[1]) < min_chars or _size(lines, out[-1][0], out[-1][1]) < min_chars):
            prev = out[-1]
            common = _common(prev[2], p[2])
            names = (prev[3] or prev[2][len(common):len(common) + 1]) + p[2][len(common):len(common) + 1]
            out[-1] = [prev[0], p[1], common, [n for n in names if n]]
        else:
            out.append(list(p))
    return out


def split(text: str, target: int = TARGET_CHARS, min_chars: int = MIN_CHARS, ceiling: int = None) -> list:
    """Sections of `text` as dicts {start, end (1-based, inclusive), path [headings], joined [headings], text}.
    [] when the file stays whole (small, or no headings and under the ceiling)."""
    ceiling = ceiling if ceiling is not None else 10 ** 12
    lines = text.splitlines(keepends=True)
    size = len(text)
    heads = headings(lines)
    if size <= target or (not heads and len(text.encode()) <= ceiling):
        return []
    pieces = _merge_small(_pieces(lines, heads, 0, len(lines), [], target, ceiling), lines, min_chars)
    if len(pieces) < 2:
        return []
    return [{"start": lo + 1, "end": hi, "path": path, "joined": joined, "text": "".join(lines[lo:hi])}
            for lo, hi, path, joined in pieces]


def heading_label(sec: dict) -> str:
    path = list(sec["path"])
    if sec.get("joined"):
        path.append("; ".join(sec["joined"]))
    return " > ".join(path) or "(top of file)"


def section_text(origin: Path, sec: dict) -> str:
    return (f"# {origin.name} > {heading_label(sec)}\n"
            f"{HEADER_TAG}{origin}, lines {sec['start']}-{sec['end']}. The original file is unchanged.)\n\n"
            + sec["text"])


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "top"


def folder_for(cache_dir: Path, pointer: str, origin: Path) -> Path:
    key = hashlib.sha1(str(origin).encode()).hexdigest()[:10]
    return Path(cache_dir) / "sections" / pointer / f"{_slug(origin.stem)}-{key}"


def write_sections(origin: Path, text: str, cache_dir: Path, pointer: str, ceiling: int = None) -> list:
    """Write origin's section files (and their line labels) and return
    [(section_path, {"file", "heading", "lines"})]; [] when the file stays whole. A section file is only
    rewritten when its text changed (an unchanged section keeps its sha, so its review is reused);
    section files of this origin that the new split no longer makes are removed."""
    secs = split(text, ceiling=ceiling)
    folder = folder_for(cache_dir, pointer, origin)
    out, keep = [], set()
    if secs:
        folder.mkdir(parents=True, exist_ok=True)
        recs = labels.label_text(text, str(origin))
        for k, sec in enumerate(secs, 1):
            p = folder / f"{k:03d}-{_slug(sec['path'][-1] if sec['path'] else 'top')}.md"
            body = section_text(origin, sec)
            if not p.is_file() or p.read_text(encoding="utf-8") != body:
                p.write_text(body, encoding="utf-8")
            lab = p.with_suffix(".labels.json")
            own = [r for r in recs if sec["start"] <= r["line"] <= sec["end"]]
            doc = json.dumps({"file": str(origin), "section": heading_label(sec), "lines": [sec["start"], sec["end"]],
                              "labels": own}, indent=2, ensure_ascii=False) + "\n"
            if not lab.is_file() or lab.read_text(encoding="utf-8") != doc:
                lab.write_text(doc, encoding="utf-8")
            keep |= {p.name, lab.name}
            out.append((p, {"file": str(origin), "heading": heading_label(sec), "lines": [sec["start"], sec["end"]]}))
    if folder.is_dir():
        for old in folder.iterdir():
            if old.name not in keep:
                old.unlink()
        if not keep:
            folder.rmdir()
    return out


HEADER_LINES = 3  # the heading-path line, the "(Section of ...)" line and a blank line


def origin_of(path) -> dict | None:
    """{"file", "heading", "lines"} for a section file under a prepare-cache sections/ folder (read from its
    own header lines), else None."""
    if "/sections/" not in str(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            first, second = f.readline(), f.readline()
    except (OSError, UnicodeDecodeError):
        return None
    m = re.match(r"^\(Section of (.+), lines (\d+)-(\d+)\. ", second)
    if not m or not first.startswith("# "):
        return None
    heading = first[2:].strip().split(" > ", 1)
    return {"file": m.group(1), "heading": heading[1] if len(heading) > 1 else heading[0],
            "lines": [int(m.group(2)), int(m.group(3))]}


def origin_lines(sec: dict, start: int, end: int) -> tuple:
    """A line range of a section file as the original file's lines (header lines map to the section start)."""
    lo, hi = sec["lines"]
    conv = lambda n: min(hi, max(lo, lo + n - HEADER_LINES - 1))
    return conv(start), conv(end)
