#!/usr/bin/env python3
"""Bulk preparation: inventory -> cheap writer drafts descriptions -> Jev checks them -> connect the approved set.

Usage:
  python3 prepare_bulk.py --root DIR --pointer NAME --principal AGENT [--limit 50] [--batch 10]
                          [--line 0.80] [--writer-model haiku] [--no-connect] [--no-findability]

Pipeline per run:
  1. Inventory *.md under --root (skips .bak*, profile/, documents/, logins.md, *-secret.md). Files matching
     card/password-like patterns or over the gate's size ceiling are HELD and never sent to the writer.
  2. Cache (prepare-cache/<pointer>.json) keyed by path: unchanged sha256 with a passing verdict skips steps 3-4.
  3. Writer (`claude -p --model <writer-model>`) drafts, per batch, one factual description plus one sample question
     a user would ask that this file answers. Descriptions are drafts, never trusted.
  4. Jev gate (connect_checked.gate) checks each description against the whole file. One rewrite retry on failure,
     with the verdict fed back. Still failing -> EXCEPTION list for the agent.
  5. Connect the passing set through the normal preview -> confirm path (replace:true if the pointer exists).
  6. Findability: each connected file's own sample question is navigated; the file must rank first or it is listed
     as a findability miss. Report only; no automatic loop beyond the one rewrite.
Nothing here edits original files. Cache and report land under prepare-cache/ next to this script.
"""
import argparse, hashlib, json, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from connect_checked import gate, memory  # noqa: E402

CACHE_DIR = HERE / "prepare-cache"
SECRET_RE = re.compile(r"[0-9]{4}[- ]?[0-9]{4}[- ]?[0-9]{4}[- ]?[0-9]{4}|password|passwd|api[_-]?key", re.I)
SKIP_PARTS = {"profile", "documents", "__pycache__", "node_modules", ".git"}
CEILING_BYTES = 90_000  # conservative stand-in for the gate's 32k-token ceiling


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def inventory(root: Path, limit: int):
    files, held = [], []
    for p in sorted(root.rglob("*.md")):
        if ".bak" in p.name or p.name == "logins.md" or p.name.endswith("-secret.md"):
            continue
        if any(part in SKIP_PARTS or part.startswith(".") for part in p.relative_to(root).parts):
            continue
        b = p.read_bytes()
        if not b.strip():
            continue
        if len(b) > CEILING_BYTES:
            held.append((str(p), "over size ceiling; split first")); continue
        if SECRET_RE.search(b.decode("utf-8", "replace")):
            held.append((str(p), "card/password-like text; review before onboarding")); continue
        files.append(p)
        if len(files) >= limit:
            break
    return files, held


def excerpt(p: Path) -> dict:
    text = p.read_text(errors="replace")
    heads = [l.strip() for l in text.splitlines() if l.startswith("#")][:15]
    return {"path": str(p), "headings": heads, "start": text[:1200]}


def writer(items: list, model: str, feedback: dict | None = None) -> dict:
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
    for attempt in range(2):
        r = subprocess.run(["claude", "-p", "--model", model], input=prompt, capture_output=True, text=True)
        m = re.search(r"\[.*\]", r.stdout, re.S)
        if m:
            try:
                arr = json.loads(m.group(0))
                return {x["path"]: x for x in arr if isinstance(x, dict) and x.get("path")}
            except Exception:
                pass
    return {}


def navigate(pointer: str, principal: str, question: str) -> list:
    out = memory({"action": "navigate", "pointer": pointer, "principal": principal, "question": question})
    return [c.get("originalPath") for c in out.get("candidates", [])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True); ap.add_argument("--pointer", required=True)
    ap.add_argument("--principal", required=True); ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--batch", type=int, default=10); ap.add_argument("--line", type=float, default=0.80)
    ap.add_argument("--writer-model", default="haiku"); ap.add_argument("--no-connect", action="store_true")
    ap.add_argument("--no-findability", action="store_true")
    a = ap.parse_args()
    if a.limit > 50:
        print("REFUSED: connect accepts at most 50 files per request; use --limit <= 50 or one pointer per folder"); return 2

    root = Path(a.root).resolve()
    CACHE_DIR.mkdir(exist_ok=True)
    cache_path = CACHE_DIR / f"{a.pointer}.json"
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    t0 = time.time()

    files, held = inventory(root, a.limit)
    print(f"inventory: {len(files)} files to prepare, {len(held)} held")
    for p, why in held:
        print(f"  HELD  {Path(p).relative_to(root)}  ({why})")

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
        got = writer([excerpt(p) for p in batch], a.writer_model)
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
            redo = writer([excerpt(p)], a.writer_model, feedback=fb).get(str(p))
            if redo and redo.get("description"):
                v2 = gate(redo["description"], str(p))
                if v2["state"] == "SUPPORTED" and v2.get("confidence", 0) >= a.line:
                    d, v, ok = redo, v2, True
        cache[str(p)] = {"sha256": sha(p), "description": d["description"], "question": d.get("question", ""),
                         "verdict": v["state"], "confidence": v.get("confidence"), "pass": ok,
                         "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        tag = "PASS" if ok else v["state"]
        print(f"  {tag:14}{v.get('confidence', ''):>5}  {p.relative_to(root)}")
        if ok:
            passing.append(p)
        else:
            exceptions.append((str(p), f"{v['state']} {v.get('confidence', '')} {v.get('reason', '')}".strip()))
    cache_path.write_text(json.dumps(cache, indent=1))

    connect_set = reused + passing
    print(f"\napproved: {len(connect_set)}  exceptions: {len(exceptions)}  held: {len(held)}")
    for p, why in exceptions:
        print(f"  EXCEPTION  {Path(p).relative_to(root)}  ({why})")

    report = {"pointer": a.pointer, "root": str(root), "approved": [str(p) for p in connect_set],
              "exceptions": exceptions, "held": held, "findability": None, "connected": False}
    if a.no_connect or not connect_set:
        (CACHE_DIR / f"{a.pointer}-report.json").write_text(json.dumps(report, indent=1))
        print(f"no connect ({'--no-connect' if a.no_connect else 'nothing approved'}); {time.time() - t0:.0f}s"); return 0

    req = {"action": "connect", "pointer": a.pointer, "principals": [a.principal],
           "sources": [{"path": str(p), "description": cache[str(p)]["description"]} for p in connect_set]}
    known = memory({"action": "panel", "principal": a.principal})
    if any((x.get("pointer") if isinstance(x, dict) else x) == a.pointer for x in known.get("pointers", [])):
        req["replace"] = True
    prev = memory(req)
    if prev.get("status") != "preparation-required":
        print("connect preview failed:", json.dumps(prev)[:300]); return 1
    hashes = {x["path"]: x["sha256"] for x in prev["sources"]}
    for s in req["sources"]:
        s["sha256"] = hashes[s["path"]]
    req["reviewed"] = True
    reg = memory(req)
    report["connected"] = reg.get("status") == "registered"
    print(f"connect: {reg.get('status')} pointer={reg.get('pointer')} sources={len(reg.get('sources', []))}")
    if not report["connected"]:
        print(json.dumps(reg)[:300]); return 1

    if not a.no_findability:
        hits, misses = 0, []
        for p in connect_set:
            q = cache[str(p)].get("question") or ""
            if not q:
                misses.append((str(p), "no question")); continue
            top = navigate(a.pointer, a.principal, q)
            if top and top[0] == str(p):
                hits += 1
            else:
                misses.append((str(p), f"ranked {'#' + str(top.index(str(p)) + 1) if str(p) in top else 'absent'}; top={Path(top[0]).name if top else 'none'}"))
        report["findability"] = {"hits": hits, "total": len(connect_set), "misses": misses}
        print(f"findability: {hits}/{len(connect_set)} files rank first on their own question")
        for p, why in misses:
            print(f"  MISS  {Path(p).relative_to(root)}  ({why})")
    (CACHE_DIR / f"{a.pointer}-report.json").write_text(json.dumps(report, indent=1))
    print(f"done in {time.time() - t0:.0f}s; report -> {CACHE_DIR / (a.pointer + '-report.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
