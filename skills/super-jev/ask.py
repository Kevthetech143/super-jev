#!/usr/bin/env python3
"""Front door for the super-jev harness: one cache-first lookup loop, any agent.

  ask.py --principal AGENT "question"
      Cache-first via the harness `cached` action (zero provider calls): a hit
      prints the answer + evidence and stops. A miss navigates every visible
      pointer in parallel, classifying each candidates/no-candidates/error. A
      non-candidate pointer prints its own status line (e.g. "[pointer]
      refresh-required") -- errors never hide as "no-candidates". All-empty ->
      a no-candidates hint naming references/connectors.md and --add. Any
      error -> "unresolved: N of M pointers errored", exit 1.

  ask.py --principal AGENT --approve "question" "answer"
      Re-searches the last lookup's top pointer and approves it, quotes taken
      verbatim from reviewedText. Next ask of the same question is a cache
      hit. Nothing ready -> hints --add.

  ask.py --principal AGENT --add "question" "answer" [--source /path]
      Manual entry, no file needed: writes one record, connects it as its own
      one-file pointer "<principal>-manual-<10 hex question hash>", approves
      it. Never replaces a pointer or touches another pointer's answers; many
      small manual pointers are fine, `cached` checks them all. Same wording
      twice refuses. --source records that file's path+sha256; a later hit on
      this pointer re-hashes it and WARNs (still answers) if changed.

  ask.py --principal AGENT --miss "question" "where it actually was"
      Log-only: the answer was found somewhere ask.py didn't reach.

AGENT can also come from SUPERJEV_PRINCIPAL. State lives under
$SUPERJEV_STATE_DIR or ~/.local/state/super-jev/<principal>/, never in this repo.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SKILL = Path(__file__).resolve().parent / "dispatch.py"

def state_dir(principal: str) -> Path:
    root = os.environ.get("SUPERJEV_STATE_DIR")
    base = Path(root).expanduser() if root else Path.home() / ".local/state/super-jev"
    return base / principal

def memory(req: dict) -> dict:
    r = subprocess.run([sys.executable, str(SKILL), "memory", "--input", "/dev/stdin"], input=json.dumps(req), capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"status": "error", "raw": (r.stdout + r.stderr)[-300:]}

def log(sdir: Path, kind: str, **fields) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": kind, **fields}
    (sdir / "lookups.jsonl").open("a").write(json.dumps(entry) + "\n")

def my_pointers(principal: str) -> list:
    panel = memory({"action": "panel", "principal": principal})
    names = [(p.get("pointer") if isinstance(p, dict) else p) for p in panel.get("pointers", [])]
    return [n for n in names if n]

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def warn_if_source_changed(record_path: Path) -> None:
    if not record_path.is_file():
        return
    source_path = source_hash = None
    for line in record_path.read_text().splitlines():
        if line.startswith("source_path:"):
            source_path = line.split(":", 1)[1].strip()
        elif line.startswith("source_sha256:"):
            source_hash = line.split(":", 1)[1].strip()
    if source_path and source_hash and Path(source_path).is_file() and sha256_file(Path(source_path)) != source_hash:
        print(f"WARNING: source changed since this answer was recorded ({source_path})")

def print_hit(hit: dict, sdir: Path) -> None:
    print("CACHE HIT")
    print("answer:", hit.get("answer") or "")
    manual_dir, seen = str(sdir / "manual"), set()
    for e in (hit.get("evidence") or [])[:3]:
        print("  evidence:", e.get("sourceId", ""), "|", str(e.get("quote", ""))[:120])
        path = e.get("path")
        if path and path not in seen and path.startswith(manual_dir + os.sep):
            seen.add(path)
            warn_if_source_changed(Path(path))

def lookup(question: str, principal: str, sdir: Path) -> int:
    t0 = time.time()
    cache = memory({"action": "cached", "principal": principal, "question": question})
    if cache.get("status") == "verified-cache-hit":
        print_hit(cache, sdir)
        log(sdir, "lookup", question=question, result="cache-hit", secs=round(time.time() - t0, 1))
        return 0
    pointers = my_pointers(principal)

    def nav(ptr):
        out = memory({"action": "navigate", "pointer": ptr, "principal": principal, "question": question})
        status = out.get("status")
        if status == "candidates" and out.get("candidates"):
            return ptr, "candidates", out["candidates"]
        if status in ("candidates", "no-candidates"):
            return ptr, "no-candidates", []
        return ptr, status or "error", []

    results = list(ThreadPoolExecutor(max_workers=8).map(nav, pointers)) if pointers else []
    merged, errored, statuses = [], 0, {}
    for ptr, kind, rows in results:
        if kind == "candidates":
            statuses[ptr] = "candidates"
            merged += [(c.get("score", 0), c.get("originalPath", ""), ptr) for c in rows]
        elif kind == "no-candidates":
            statuses[ptr] = "no-candidates"
        else:
            errored += 1
            statuses[ptr] = kind
            print(f"[{ptr}] {kind}")
    merged.sort(reverse=True)
    top = merged[:5]
    log(sdir, "lookup", question=question, pointers=len(pointers), statuses=statuses, secs=round(time.time() - t0, 1),
        top=[{"score": s, "path": p, "pointer": ptr} for s, p, ptr in top])
    if errored:
        print(f"unresolved: {errored} of {len(pointers)} pointers errored")
        return 1
    if not top:
        print(f"no-candidates across {len(pointers)} pointers. Connect sources (see references/connectors.md) or record a fact with --add.")
        return 0
    for s, p, ptr in top:
        print(f"{s:5.2f}  {p}  [{ptr}]")
    return 0

def find_pointer(sdir: Path, question: str):
    path = sdir / "lookups.jsonl"
    if not path.is_file():
        return None
    for line in reversed(path.read_text().splitlines()):
        rec = json.loads(line)
        if rec.get("kind") == "lookup" and rec.get("question") == question and rec.get("top"):
            return rec["top"][0]["pointer"]
    return None

def approve(principal: str, question: str, answer: str, sdir: Path, pointer=None) -> int:
    pointer = pointer or find_pointer(sdir, question)
    if not pointer:
        print("no prior lookup with candidates for that question; run ask first, or use --add")
        return 1
    out = memory({"action": "search", "pointer": pointer, "principal": principal, "question": question})
    if out.get("status") == "verified-cache-hit":
        print("already cached")
        return 0
    if out.get("status") != "ready":
        print(f"cannot approve: search returned {out.get('status')} on {pointer}. Use --add to record it manually.")
        log(sdir, "approve", question=question, pointer=pointer, result=out.get("status"))
        return 1
    evidence = [{"sourceId": p["sourceId"], "quote": p["reviewedText"]} for p in out.get("passages", [])[:3] if p.get("reviewedText")]
    res = memory({"action": "approve", "ticket": out["approvalTicket"], "principal": principal, "approved": True, "answer": answer, "evidence": evidence})
    ok = res.get("status") in ("approved", "saved", "ok")
    print("approve:", res.get("status"), "" if ok else json.dumps(res)[:200])
    log(sdir, "approve", question=question, pointer=pointer, result=res.get("status"))
    return 0 if ok else 1

def add_manual(principal: str, question: str, answer: str, source, sdir: Path) -> int:
    pointer = f"{principal}-manual-{hashlib.sha1(question.encode()).hexdigest()[:10]}"
    if pointer in my_pointers(principal):
        print(f"refused: {pointer} already exists for this question wording; use different wording, or remove the pointer explicitly.")
        return 1
    manual_dir = sdir / "manual"
    manual_dir.mkdir(parents=True, exist_ok=True)
    record = manual_dir / f"{pointer}.md"
    lines = [f"# {question}", ""]
    if source and Path(source).is_file():
        src = Path(source).resolve()
        lines += [f"source_path: {src}", f"source_sha256: {sha256_file(src)}", ""]
    lines += [answer, "", f"recorded: {time.strftime('%Y-%m-%d %H:%M %Z')}"]
    record.write_text("\n".join(lines) + "\n")
    req = {"action": "connect", "pointer": pointer, "principals": [principal], "sources": [{"path": str(record), "description": question[:120]}]}
    preview = memory(req)
    if preview.get("status") != "preparation-required" or "sources" not in preview:
        print("connect preview failed:", json.dumps(preview)[:300])
        return 1
    hashes = {x["path"]: x["sha256"] for x in preview["sources"]}
    for s in req["sources"]:
        s["sha256"] = hashes[s["path"]]
    req["reviewed"] = True
    reg = memory(req)
    if reg.get("status") != "registered":
        print("connect failed:", json.dumps(reg)[:300])
        return 1
    print(f"manual entry written: {record.name}; pointer {pointer} registered")
    return approve(principal, question, answer, sdir, pointer=pointer)

def resolve_principal(args: list) -> tuple[str, list]:
    if "--principal" in args:
        i = args.index("--principal")
        return args[i + 1], args[:i] + args[i + 2:]
    return os.environ.get("SUPERJEV_PRINCIPAL", ""), args

def main() -> int:
    principal, a = resolve_principal(sys.argv[1:])
    if not principal or not a:
        print(__doc__)
        return 2
    sdir = state_dir(principal)
    if a[0] == "--miss":
        log(sdir, "miss", question=a[1], actual=" ".join(a[2:]))
        print("miss recorded")
        return 0
    if a[0] == "--approve":
        return approve(principal, a[1], a[2], sdir)
    if a[0] == "--add":
        src = a[a.index("--source") + 1] if "--source" in a else None
        end = a.index("--source") if "--source" in a else len(a)
        return add_manual(principal, a[1], " ".join(a[2:end]), src, sdir)
    return lookup(" ".join(a), principal, sdir)

if __name__ == "__main__":
    sys.exit(main())
