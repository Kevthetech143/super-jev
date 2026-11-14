"""Connect-time LINE LABELER: deterministic, domain-general facts about every line of a text file.

Per line: file, line number, type (heading/item/table_row/note/link), section path (headings above it),
caption (the note line that introduces a list), dates (raw + normalized), subject (folder path + front
matter), links to other files. No judgments (current/stopped/importance) are labeled.

Owner files are only READ. Labels live in a separate sidecar index (one JSON per file + manifest.json);
a file is relabeled only when its sha256 changes.

CLI:  python3 labels.py build --root DIR --out INDEX_DIR [--glob '*.md']
"""
import argparse, fnmatch, hashlib, json, os, re, sys, time

TEXT_EXT = (".md", ".txt", ".markdown")
SKIP_NAME = re.compile(r"(secret|login|password|credential|\.env|\.bak)", re.I)

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_RES = [
    ("iso", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    ("mdy", re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])")),
    ("mon_dy", re.compile(r"\b(" + _MON + r")\.?\s*(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})\b", re.I)),
    ("d_mon_y", re.compile(r"\b(\d{1,2})\s+(" + _MON + r")\.?,?\s+(\d{4})\b", re.I)),
    ("mon_d", re.compile(r"\b(" + _MON + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!,?\s*\d{4})(?![:\d])", re.I)),
    ("md", re.compile(r"(?<![\d/.$])(\d{1,2})/(\d{1,2})(?![\d/%])")),
]
LINK_RES = [
    re.compile(r"\[[^\]]*\]\(([^)\s]+)\)"),                      # markdown link
    re.compile(r"\[\[([^\]|#]+)"),                                # wiki link
    re.compile(r"`([^`\s]+\.[A-Za-z0-9]{1,5})`"),                 # `path.ext`
    re.compile(r"(?<![\w/.@])((?:\.\.?/)?(?:[\w.-]+/)*[\w.-]+\.(?:md|txt|pdf|png|jpe?g|csv|json|docx?|xlsx?))\b"),
]
ITEM_RE = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+(?!\s*$)")
HEAD_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _ok_date(y, m, d):
    return 1 <= m <= 12 and 1 <= d <= 31 and (y is None or 1900 <= y <= 2100)


def find_dates(text):
    """Dates written on the line, copied exactly, with a normalized ISO form (--MM-DD when no year)."""
    out, taken = [], []
    for kind, rx in DATE_RES:
        for m in rx.finditer(text):
            s, e = m.span()
            if any(s < te and e > ts for ts, te in taken):
                continue
            g = m.groups()
            try:
                if kind == "iso":
                    y, mo, d = int(g[0]), int(g[1]), int(g[2])
                elif kind == "mdy":
                    mo, d, y = int(g[0]), int(g[1]), int(g[2])
                    y = y + 2000 if y < 100 else y
                elif kind == "mon_dy":
                    mo, d, y = MONTHS[g[0].lower()[:3]], int(g[1]), int(g[2])
                elif kind == "d_mon_y":
                    d, mo, y = int(g[0]), MONTHS[g[1].lower()[:3]], int(g[2])
                elif kind == "mon_d":
                    mo, d, y = MONTHS[g[0].lower()[:3]], int(g[1]), None
                else:
                    mo, d, y = int(g[0]), int(g[1]), None
            except (KeyError, ValueError):
                continue
            if not _ok_date(y, mo, d):
                continue
            norm = f"{y:04d}-{mo:02d}-{d:02d}" if y else f"--{mo:02d}-{d:02d}"
            out.append({"raw": m.group(0), "norm": norm, "pos": s})
            taken.append((s, e))
    out.sort(key=lambda x: x["pos"])
    for x in out:
        del x["pos"]
    return out


def find_links(text, file_dir=None):
    seen, out = set(), []
    for rx in LINK_RES:
        for m in rx.finditer(text):
            raw = m.group(1).strip()
            if raw in seen or raw.startswith(("http://", "https://", "mailto:")) or re.fullmatch(r"[\d.]+", raw):
                continue
            seen.add(raw)
            item = {"raw": raw}
            if file_dir:
                p = os.path.normpath(os.path.join(file_dir, raw))
                if os.path.exists(p):
                    item["resolved"] = p
            out.append(item)
    return out


def _caps_heading(s):
    """A short standalone line written mostly in capitals (e.g. 'CARDIAC / BP') acts as a heading."""
    core = re.sub(r"\([^)]*\)", "", s).strip().rstrip(":")
    letters = [c for c in core if c.isalpha()]
    return (2 <= len(letters) and len(core) <= 60 and sum(c.isupper() for c in letters) / len(letters) >= 0.9
            and not core.endswith("."))


def line_type(s):
    if HEAD_RE.match(s):
        return "heading"
    if TABLE_RE.match(s):
        return "table_row"
    if ITEM_RE.match(s):
        return "item"
    body = re.sub(r"^\s*(?:see also|related|links?)\s*:\s*", "", s, flags=re.I)
    if body != s or (find_links(s) and len(re.sub(r"\[[^\]]*\]\([^)]*\)|`[^`]*`|[\s,;]", "", body)) < 0.5 * len(s) and
                     re.fullmatch(r"[\s\S]*?(\.md|\.txt|\.pdf|\))\W*", s.strip())):
        return "link"
    if _caps_heading(s):
        return "heading"
    return "note"


def front_matter(lines):
    if not lines or lines[0].strip() != "---":
        return {}, 0
    meta = {}
    for i, l in enumerate(lines[1:], 1):
        if l.strip() in ("---", "..."):
            return meta, i + 1
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", l)
        if m:
            meta[m.group(1)] = m.group(2).strip().strip("'\"")
    return {}, 0


def subject_of(path, root, meta):
    rel = os.path.relpath(path, root) if root else os.path.basename(path)
    parts = rel.split(os.sep)[:-1]
    subj = {"folders": parts}
    for k in ("subject", "person", "patient", "client", "customer", "vendor", "owner", "title"):
        if k in meta:
            subj[k] = meta[k]
    return subj


def label_text(text, path="<text>", root=None):
    lines = text.split("\n")
    meta, body_start = front_matter(lines)
    subj = subject_of(path, root, meta)
    fdir = os.path.dirname(os.path.abspath(path)) if path != "<text>" else None
    stack, out, caption, table_head = [], [], None, None
    for i, raw in enumerate(lines, 1):
        if i <= body_start:
            continue
        s = raw.rstrip()
        quoted = bool(re.match(r"^\s*>", s))
        s_in = re.sub(r"^\s*(?:>\s?)+", "", s) if quoted else s
        if not s_in.strip():
            caption = None if not quoted else caption
            continue
        t = line_type(s_in)
        if t == "table_row" and TABLE_SEP_RE.match(s_in):
            continue
        if t == "heading" and not quoted:  # a heading inside a > banner does not scope the lines after it
            hm = HEAD_RE.match(s_in)
            lvl, title = (len(hm.group(1)), hm.group(2)) if hm else (7, s_in.strip().rstrip(":"))
            stack = [h for h in stack if h[0] < lvl] + [(lvl, title)]
            caption, table_head = None, None
        rec = {"file": path, "line": i, "type": t, "text": s_in.strip(),
               "section": [h[1] for h in stack[:-1]] if (t == "heading" and not quoted) else [h[1] for h in stack],
               "dates": find_dates(s_in), "subject": subj, "links": find_links(s_in, fdir)}
        if quoted:
            rec["quoted"] = True
        if t == "item" and caption:
            rec["caption"] = caption
        if t == "table_row":
            cells = [c.strip() for c in s_in.strip().strip("|").split("|")]
            if table_head is None:
                table_head = cells
                rec["table_header"] = True
            else:
                rec["columns"] = dict(zip(table_head, cells))
        else:
            table_head = None if t != "table_row" else table_head
        if t == "note":
            caption = s_in.strip()
        elif t != "item":
            caption = None
        out.append(rec)
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def _key(path):
    return hashlib.sha1(path.encode()).hexdigest()[:16] + ".json"


def build_index(root, out_dir, glob=None):
    """Label every text file under root into out_dir. Unchanged files (same sha) are skipped."""
    root = os.path.abspath(os.path.expanduser(root))
    out_dir = os.path.abspath(os.path.expanduser(out_dir))
    if out_dir.startswith(root + os.sep) or out_dir == root:
        raise ValueError("index must live outside the owner's folder")
    os.makedirs(out_dir, exist_ok=True)
    man_p = os.path.join(out_dir, "manifest.json")
    manifest = json.load(open(man_p)) if os.path.exists(man_p) else {}
    stats = {"labeled": 0, "unchanged": 0, "lines": 0, "removed": 0}
    seen = set()
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith(".") and not SKIP_NAME.search(d)]
        for fn in fns:
            if not fn.lower().endswith(TEXT_EXT) or SKIP_NAME.search(fn) or (glob and not fnmatch.fnmatch(fn, glob)):
                continue
            p = os.path.join(dp, fn)
            seen.add(p)
            sh = sha256(p)
            if manifest.get(p, {}).get("sha") == sh and os.path.exists(os.path.join(out_dir, _key(p))):
                stats["unchanged"] += 1
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                recs = label_text(f.read(), p, root)
            with open(os.path.join(out_dir, _key(p)), "w") as f:
                f.write(json.dumps({"file": p, "sha": sh, "lines": recs}, indent=2, ensure_ascii=False) + "\n")
            manifest[p] = {"sha": sh, "key": _key(p), "n": len(recs)}
            stats["labeled"] += 1
            stats["lines"] += len(recs)
    for p in [p for p in manifest if p not in seen]:
        try:
            os.remove(os.path.join(out_dir, manifest[p]["key"]))
        except OSError:
            pass
        del manifest[p]
        stats["removed"] += 1
    with open(man_p, "w") as f:
        f.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return stats


def load_labels(path, out_dir):
    path = os.path.abspath(os.path.expanduser(path))
    p = os.path.join(os.path.expanduser(out_dir), _key(path))
    return json.load(open(p))["lines"] if os.path.exists(p) else None


def render_items(recs, notes_too=True):
    """Compact model input: item lines grouped under their section (printed once), dates normalized; notes separate."""
    items, notes, last = [], [], None
    for r in recs:
        sec = " > ".join(r["section"][-2:] + ([r["caption"]] if r.get("caption") else []))
        if r["type"] in ("item", "table_row") and not r.get("table_header") and not r.get("quoted"):
            if sec != last:
                items.append(f"[{sec}]" if sec else "[no section]")
                last = sec
            ds = ", ".join(sorted({d["norm"] for d in r["dates"]}))
            items.append(f'L{r["line"]} {r["text"]}' + (f"  (dates: {ds})" if ds else ""))
        elif r["type"] == "note":
            notes.append(f'L{r["line"]} {r["text"]}')
    if not notes_too:
        return "ITEM LINES:\n" + "\n".join(items)
    return "ITEM LINES:\n" + "\n".join(items) + "\n\nNOTES (not items):\n" + "\n".join(notes)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("--root", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--glob")
    a = ap.parse_args(argv)
    t = time.time()
    st = build_index(a.root, a.out, a.glob)
    st["seconds"] = round(time.time() - t, 2)
    print(json.dumps(st))


if __name__ == "__main__":
    main()
