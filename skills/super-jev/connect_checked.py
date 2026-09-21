#!/usr/bin/env python3
"""Checked connect: gate every description against its file with Jev, then connect only if all pass.

Usage:
  python3 connect_checked.py CONNECT.json            # check, then preview + confirm connect on all-pass
  python3 connect_checked.py CONNECT.json --check-only
  python3 connect_checked.py CONNECT.json --line 0.80 # confidence line (default: the gate's own 0.80)

CONNECT.json is the normal memory connect request: {"action":"connect","pointer":...,"principals":[...],
"sources":[{"path":..., "description":...}, ...]}. Every source must carry a description.

Rules (deliberately strict):
  PASS      = gate says SUPPORTED at or above the line.
  FAIL      = NOT_SUPPORTED at any confidence, or SUPPORTED under the line (the gate's own "read it" zone).
  UNCHECKED = file over the gate's 32k-token ceiling; the gate refuses to truncate, so we refuse to connect it.
Any FAIL or UNCHECKED refuses the whole connect. Fix the description, split the file, or drop it, then rerun.
Verdicts are written next to CONNECT.json as <name>.verdicts.json with each file's sha256, so a later run
can tell which files changed since they were last checked.
"""
import hashlib, json, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CEILING_MSG = "exceeds the 32,768-token ceiling"


def gate(description: str, path: str) -> dict:
    t = time.time()
    r = subprocess.run([sys.executable, str(HERE / "dispatch.py"), "check", "--claim", description, path],
                       capture_output=True, text=True)
    txt = r.stdout + r.stderr
    secs = round(time.time() - t, 1)
    if CEILING_MSG in txt:
        return {"state": "UNCHECKED", "reason": "over 32k-token ceiling; split the file", "secs": secs}
    m = re.search(r"\bc1\s+(SUPPORTED|NOT_SUPPORTED)\s+([\d.]+)", txt)
    if not m:
        tail = txt.strip().splitlines()[-1:] or ["no output"]
        return {"state": "ERROR", "reason": tail[0][:160], "secs": secs}
    return {"state": m.group(1), "confidence": float(m.group(2)), "secs": secs}


def memory(req: dict) -> dict:
    r = subprocess.run([sys.executable, str(HERE / "dispatch.py"), "memory", "--input", "/dev/stdin"],
                       input=json.dumps(req), capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"status": "error", "raw": (r.stdout + r.stderr)[-400:]}


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__); return 2
    req_path = Path(args[0]).resolve()
    check_only = "--check-only" in args
    line = float(args[args.index("--line") + 1]) if "--line" in args else 0.80
    req = json.loads(req_path.read_text())
    sources = req.get("sources", [])
    if not sources or any(not s.get("description", "").strip() for s in sources):
        print("REFUSED: every source needs a non-empty description"); return 2

    verdicts, failures = [], []
    for s in sources:
        p = Path(s["path"])
        if not p.is_file():
            v = {"state": "ERROR", "reason": "file not found", "secs": 0}
        else:
            v = gate(s["description"], str(p))
        v["path"] = str(p)
        v["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
        ok = v["state"] == "SUPPORTED" and v.get("confidence", 0) >= line
        v["pass"] = ok
        verdicts.append(v)
        tag = "PASS" if ok else v["state"]
        conf = f" {v['confidence']:.2f}" if "confidence" in v else ""
        extra = f" ({v['reason']})" if v.get("reason") else ""
        print(f"{tag:10}{conf:6} {v['secs']:4}s  {p.name}{extra}")
        if not ok:
            failures.append(p.name)

    out = req_path.with_suffix(".verdicts.json")
    out.write_text(json.dumps({"checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "line": line,
                               "verdicts": verdicts}, indent=1))
    print(f"\nverdicts -> {out}")

    if failures:
        print(f"REFUSED: {len(failures)} of {len(sources)} descriptions did not pass: {', '.join(failures)}")
        print("Fix the description, split the file, or drop it. Nothing was connected.")
        return 1
    print(f"ALL PASS: {len(sources)}/{len(sources)} descriptions supported at >= {line}")
    if check_only:
        return 0

    preview = memory(req)
    if preview.get("status") != "preparation-required" or "sources" not in preview:
        print("connect preview failed:", json.dumps(preview)[:400]); return 1
    hashes = {x["path"]: x["sha256"] for x in preview["sources"]}
    for s in req["sources"]:
        s["sha256"] = hashes[s["path"]]
    req["reviewed"] = True
    reg = memory(req)
    print("connect:", reg.get("status"), "pointer:", reg.get("pointer"), "sources:", len(reg.get("sources", [])))
    return 0 if reg.get("status") == "registered" else 1


if __name__ == "__main__":
    sys.exit(main())
