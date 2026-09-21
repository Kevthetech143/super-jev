#!/usr/bin/env python3
"""Bulk preparation: inventory -> cheap writer drafts descriptions -> Jev checks them -> connect the approved set.

Usage:
  python3 prepare_bulk.py --root DIR [--root DIR2 ...] --pointer NAME --principal AGENT
                          [--exclude SUBPATH ...] [--no-recurse] [--limit 50] [--max-files 250]
                          [--batch 10] [--line 0.80] [--writer-model haiku]
                          [--writer-command 'COMMAND [ARG ...]'] [--no-connect] [--no-findability]
                          [--refresh]

Pipeline per run:
  1. Inventory *.md under the union of one or more --root directories, in the order given (repeat --root for
     a whole agent brain spanning several folders). Skips .bak*, profile/, documents/, logins.md, *-secret.md
     and hidden directories; --exclude SUBPATH (repeatable) also skips any file whose path relative to its
     root starts with that subpath; --no-recurse limits each root to its direct children only. Files matching
     card/password-like patterns or over the gate's size ceiling are HELD and never sent to the writer; a
     per-file reason (and, for the secret-pattern case, the matching line's pattern type and line number with
     all digits masked) is written to prepare-cache/<pointer>-held.txt for human review without opening files.
     The whole run refuses above --max-files (default 250) total files, as a size guard.
  2. Cache (prepare-cache/<pointer>.json) keyed by path: unchanged sha256 with a passing verdict skips steps 3-4.
     --refresh additionally drops any cached path that no longer exists on disk from the cache and the connect
     set, noting it in the report.
  3. Writer (by default `claude -p --model <writer-model>`) drafts, per batch, one factual description plus one sample
     question a user would ask that this file answers. Descriptions are drafts, never trusted.
  4. Jev gate (connect_checked.gate) checks each description against the whole file. One rewrite retry on
     failure, with the verdict fed back. Still failing -> EXCEPTION list for the agent.
  5. Connect the passing set through the normal preview -> confirm path (replace:true, with a one-line warning,
     if the pointer already exists). The harness accepts at most 50 files per connect request, so a set over
     --limit (default 50, hard max 50) is split into parts named <pointer>, <pointer>-2, <pointer>-3, ... in
     stable sorted-path order, each connected separately; the cache and report stay keyed by the base pointer.
  6. Findability: each connected file's own sample question is navigated; the file must rank first or it is
     listed as a findability miss. Report only; no automatic loop beyond the one rewrite.
Nothing here edits original files. Cache and report land under prepare-cache/ next to this script.
"""
import argparse, hashlib, json, re, shlex, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from connect_checked import gate, memory  # noqa: E402

CACHE_DIR = HERE / "prepare-cache"
CARD_RE = re.compile(r"[0-9]{4}[- ]?[0-9]{4}[- ]?[0-9]{4}[- ]?[0-9]{4}")
WORD_RE = re.compile(r"password|passwd|api[_-]?key", re.I)
SECRET_RE = re.compile(f"{CARD_RE.pattern}|{WORD_RE.pattern}", re.I)
SKIP_PARTS = {"profile", "documents", "__pycache__", "node_modules", ".git"}
CEILING_BYTES = 90_000  # conservative stand-in for the gate's 32k-token ceiling


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def relstr(p, roots) -> str:
    """Path relative to whichever --root contains it; falls back to the raw path."""
    pr = Path(p).resolve()
    for r in roots:
        try:
            return str(pr.relative_to(r))
        except ValueError:
            continue
    return str(p)


def _excluded(rel_posix: str, excludes: list) -> bool:
    return any(rel_posix == ex or rel_posix.startswith(ex + "/") for ex in excludes)


def inventory(roots: list, excludes: list = None, no_recurse: bool = False):
    """Union of *.md files under `roots`, in root order then sorted-per-root order. Each file is counted once
    even if reachable through more than one root."""
    excludes = [e.strip("/") for e in (excludes or []) if e.strip("/")]
    files, held, seen = [], [], set()
    for root in roots:
        glob_iter = sorted(root.glob("*.md")) if no_recurse else sorted(root.rglob("*.md"))
        for p in glob_iter:
            rp = p.resolve()
            if rp in seen:
                continue
            if ".bak" in p.name or p.name == "logins.md" or p.name.endswith("-secret.md"):
                continue
            rel_parts = p.relative_to(root).parts
            if any(part in SKIP_PARTS or part.startswith(".") for part in rel_parts):
                continue
            if _excluded(p.relative_to(root).as_posix(), excludes):
                continue
            b = p.read_bytes()
            if not b.strip():
                continue
            seen.add(rp)
            if len(b) > CEILING_BYTES:
                held.append((str(p), "over size ceiling; split first")); continue
            if SECRET_RE.search(b.decode("utf-8", "replace")):
                held.append((str(p), "card/password-like text; review before onboarding")); continue
            files.append(p)
    return files, held


def secret_detail(p: Path):
    """First matching line for a secret-pattern hold, with digits masked. Returns the pattern type and line
    number for review; never returns the raw matched text."""
    try:
        text = p.read_text(errors="replace")
    except Exception:
        return None
    for i, line in enumerate(text.splitlines(), start=1):
        if CARD_RE.search(line):
            return {"type": "card-number-like digits", "line": i, "masked": re.sub(r"\d", "#", line)}
        if WORD_RE.search(line):
            return {"type": "password/api-key keyword", "line": i, "masked": re.sub(r"\d", "#", line)}
    return None


def write_held_txt(pointer: str, held: list) -> None:
    if not held:
        return
    lines = []
    for p, why in held:
        lines.append(f"{p}\t{why}")
        if "card/password" in why:
            d = secret_detail(Path(p))
            if d:
                lines.append(f"    pattern={d['type']}  line={d['line']}  masked={d['masked']}")
    (CACHE_DIR / f"{pointer}-held.txt").write_text("\n".join(lines) + "\n")


def excerpt(p: Path) -> dict:
    text = p.read_text(errors="replace")
    heads = [l.strip() for l in text.splitlines() if l.startswith("#")][:15]
    return {"path": str(p), "headings": heads, "start": text[:1200]}


class WriterError(RuntimeError):
    """The external description writer did not produce a usable response."""


def writer(items: list, model: str, feedback: dict | None = None, command: list[str] | None = None) -> dict:
    """Run a writer that reads the prompt from stdin and returns a JSON array on stdout."""
    fb = ""
    if feedback:
        fb = ("\nPrevious drafts were REJECTED by a fact checker for these paths; write more literally from the file "
              "and do not claim anything not in it:\n" + json.dumps(feedback, indent=1))
    prompt = (
        "You write catalog descriptions for files. For EACH file below, return one JSON object with keys "
        "path, description, question.\n"
        "description: one factual sentence, at most 40 words, naming the main topics and purpose exactly as the file "
        "shows them; no praise, no guessing beyond the excerpt and headings; if the file is a stub or pointer, say so.\n"
        "question: one natural question a user would ask that THIS file answers better than any sibling file; mention a "
        "specific detail from it.\n"
        "Return ONLY a JSON array, no prose." + fb + "\n\nFILES:\n" + json.dumps(items, indent=1)
    )
    argv = command or ["claude", "-p", "--model", model]
    for attempt in range(2):
        try:
            r = subprocess.run(argv, input=prompt, capture_output=True, text=True)
        except OSError as e:
            raise WriterError(f"could not start writer: {e.strerror or e.__class__.__name__}") from e
        if r.returncode:
            raise WriterError(f"writer exited with status {r.returncode}")
        m = re.search(r"\[.*\]", r.stdout, re.S)
        if m:
            try:
                arr = json.loads(m.group(0))
                return {x["path"]: x for x in arr if isinstance(x, dict) and x.get("path")}
            except Exception:
                pass
    raise WriterError("writer returned invalid JSON after 2 attempts")


def navigate(pointer: str, principal: str, question: str) -> list:
    out = memory({"action": "navigate", "pointer": pointer, "principal": principal, "question": question})
    return [c.get("originalPath") for c in out.get("candidates", [])]


def connect_part(pointer: str, principal: str, part_files: list, cache: dict) -> dict:
    """Preview -> confirm connect for one pointer (a whole pointer or one split part of one)."""
    req = {"action": "connect", "pointer": pointer, "principals": [principal],
           "sources": [{"path": str(p), "description": cache[str(p)]["description"]} for p in part_files]}
    known = memory({"action": "panel", "principal": principal})
    if any((x.get("pointer") if isinstance(x, dict) else x) == pointer for x in known.get("pointers", [])):
        req["replace"] = True
        print(f"WARNING: replace:true on pointer {pointer} rotates that pointer's approved answers")
    prev = memory(req)
    if prev.get("status") != "preparation-required":
        print(f"connect preview failed for {pointer}:", json.dumps(prev)[:300])
        return {"connected": False}
    hashes = {x["path"]: x["sha256"] for x in prev["sources"]}
    for s in req["sources"]:
        s["sha256"] = hashes[s["path"]]
    req["reviewed"] = True
    reg = memory(req)
    connected = reg.get("status") == "registered"
    print(f"connect: {reg.get('status')} pointer={reg.get('pointer')} sources={len(reg.get('sources', []))}")
    if not connected:
        print(json.dumps(reg)[:300])
    return {"connected": connected}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", dest="roots", action="append", required=True)
    ap.add_argument("--pointer", required=True); ap.add_argument("--principal", required=True)
    ap.add_argument("--exclude", dest="excludes", action="append", default=[])
    ap.add_argument("--no-recurse", action="store_true")
    ap.add_argument("--limit", type=int, default=50); ap.add_argument("--max-files", type=int, default=250)
    ap.add_argument("--batch", type=int, default=10); ap.add_argument("--line", type=float, default=0.80)
    ap.add_argument("--writer-model", default="haiku",
                    help="model passed to the default Claude writer")
    ap.add_argument("--writer-command", metavar="COMMAND",
                    help="shell-style command for another writer; it receives the prompt on stdin and returns a JSON array on stdout")
    ap.add_argument("--no-connect", action="store_true")
    ap.add_argument("--no-findability", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    if a.limit > 50:
        print("REFUSED: connect accepts at most 50 files per pointer part; use --limit <= 50 (parts split automatically)"); return 2
    try:
        writer_command = shlex.split(a.writer_command) if a.writer_command else None
    except ValueError as e:
        print(f"REFUSED: invalid --writer-command: {e}"); return 2
    if a.writer_command and not writer_command:
        print("REFUSED: --writer-command must name a command"); return 2

    roots = [Path(r).resolve() for r in a.roots]
    CACHE_DIR.mkdir(exist_ok=True)
    cache_path = CACHE_DIR / f"{a.pointer}.json"
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    t0 = time.time()

    files, held = inventory(roots, a.excludes, a.no_recurse)
    print(f"inventory: {len(files)} files to prepare, {len(held)} held")
    for p, why in held:
        print(f"  HELD  {relstr(p, roots)}  ({why})")
    write_held_txt(a.pointer, held)

    if len(files) > a.max_files:
        print(f"REFUSED: {len(files)} files exceed --max-files {a.max_files}; narrow --root/--exclude/--no-recurse or raise --max-files")
        return 2

    removed = []
    if a.refresh:
        live = {str(p) for p in files}
        for k in list(cache.keys()):
            if k not in live and not Path(k).exists():
                removed.append(k)
                cache.pop(k, None)
        if removed:
            print(f"refresh: {len(removed)} cached files removed from disk, dropped from cache and connect set")
            for k in removed:
                print(f"  REMOVED  {relstr(k, roots)}")

    todo, reused = [], []
    for p in files:
        c = cache.get(str(p))
        if c and c.get("sha256") == sha(p) and c.get("pass"):
            reused.append(p)
        else:
            todo.append(p)
    print(f"cache: {len(reused)} unchanged and already passing, {len(todo)} to draft")

    drafts = {}
    for i in range(0, len(todo), a.batch):
        batch = todo[i:i + a.batch]
        try:
            if writer_command:
                got = writer([excerpt(p) for p in batch], a.writer_model, command=writer_command)
            else:
                got = writer([excerpt(p) for p in batch], a.writer_model)
        except WriterError as e:
            print(f"ERROR: description writer failed: {e}"); return 1
        drafts.update(got)
        print(f"writer batch {i // a.batch + 1}: {len(got)}/{len(batch)} drafted")

    exceptions, passing = [], []
    for p in todo:
        d = drafts.get(str(p))
        if not d or not d.get("description"):
            exceptions.append((str(p), "writer returned no draft")); continue
        v = gate(d["description"], str(p))
        ok = v["state"] == "SUPPORTED" and v.get("confidence", 0) >= a.line
        if not ok:
            fb = {str(p): {"draft": d["description"], "verdict": v["state"], "confidence": v.get("confidence"), "reason": v.get("reason")}}
            try:
                if writer_command:
                    redo = writer([excerpt(p)], a.writer_model, feedback=fb, command=writer_command).get(str(p))
                else:
                    redo = writer([excerpt(p)], a.writer_model, feedback=fb).get(str(p))
            except WriterError as e:
                print(f"ERROR: description writer failed: {e}"); return 1
            if redo and redo.get("description"):
                v2 = gate(redo["description"], str(p))
                if v2["state"] == "SUPPORTED" and v2.get("confidence", 0) >= a.line:
                    d, v, ok = redo, v2, True
        cache[str(p)] = {"sha256": sha(p), "description": d["description"], "question": d.get("question", ""),
                         "verdict": v["state"], "confidence": v.get("confidence"), "pass": ok,
                         "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        tag = "PASS" if ok else v["state"]
        print(f"  {tag:14}{v.get('confidence', ''):>5}  {relstr(p, roots)}")
        if ok:
            passing.append(p)
        else:
            exceptions.append((str(p), f"{v['state']} {v.get('confidence', '')} {v.get('reason', '')}".strip()))
    cache_path.write_text(json.dumps(cache, indent=1))

    connect_set = reused + passing
    print(f"\napproved: {len(connect_set)}  exceptions: {len(exceptions)}  held: {len(held)}")
    for p, why in exceptions:
        print(f"  EXCEPTION  {relstr(p, roots)}  ({why})")

    report = {"pointer": a.pointer, "roots": [str(r) for r in roots], "approved": [str(p) for p in connect_set],
              "exceptions": exceptions, "held": held, "removed": removed, "findability": None,
              "connected": False, "parts": []}
    if a.no_connect or not connect_set:
        (CACHE_DIR / f"{a.pointer}-report.json").write_text(json.dumps(report, indent=1))
        print(f"no connect ({'--no-connect' if a.no_connect else 'nothing approved'}); {time.time() - t0:.0f}s"); return 0

    ordered = sorted(connect_set, key=str)
    parts = [ordered[i:i + a.limit] for i in range(0, len(ordered), a.limit)]
    if len(parts) > 1:
        print(f"splitting {len(ordered)} approved files into {len(parts)} parts of at most {a.limit}")

    all_connected = True
    hits, total, misses = 0, 0, []
    for idx, part_files in enumerate(parts):
        pname = a.pointer if idx == 0 else f"{a.pointer}-{idx + 1}"
        result = connect_part(pname, a.principal, part_files, cache)
        report["parts"].append({"pointer": pname, "count": len(part_files), "connected": result["connected"]})
        if not result["connected"]:
            all_connected = False
            continue
        if not a.no_findability:
            for p in part_files:
                q = cache[str(p)].get("question") or ""
                total += 1
                if not q:
                    misses.append((str(p), "no question")); continue
                top = navigate(pname, a.principal, q)
                if top and top[0] == str(p):
                    hits += 1
                else:
                    misses.append((str(p), f"ranked {'#' + str(top.index(str(p)) + 1) if str(p) in top else 'absent'}; top={Path(top[0]).name if top else 'none'}"))
    report["connected"] = all_connected

    if not a.no_findability:
        report["findability"] = {"hits": hits, "total": total, "misses": misses}
        print(f"findability: {hits}/{total} files rank first on their own question")
        for p, why in misses:
            print(f"  MISS  {relstr(p, roots)}  ({why})")

    (CACHE_DIR / f"{a.pointer}-report.json").write_text(json.dumps(report, indent=1))
    print(f"done in {time.time() - t0:.0f}s; report -> {CACHE_DIR / (a.pointer + '-report.json')}")
    return 0 if all_connected else 1


if __name__ == "__main__":
    sys.exit(main())
