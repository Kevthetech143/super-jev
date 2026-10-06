#!/usr/bin/env python3
"""Checked connect: gate every description against its file with Jev, then connect only if all pass.

Usage:
  python3 connect_checked.py CONNECT.json            # check, then preview + confirm connect on all-pass
  python3 connect_checked.py CONNECT.json --check-only
  python3 connect_checked.py CONNECT.json --line 0.80 # confidence line (default: the judge profile's line)

CONNECT.json is the normal memory connect request: {"action":"connect","pointer":...,"principals":[...],
"sources":[{"path":..., "description":...}, ...]}. Every source must carry a description.

Rules (deliberately strict):
  PASS      = gate says SUPPORTED at or above the line.
  FAIL      = NOT_SUPPORTED or CONTRADICTED at any confidence, or SUPPORTED under the line (the gate's own
              "read it" zone).
  UNCHECKED = file over the gate's judge-token ceiling; the gate refuses to truncate, so we refuse to connect it.
Any FAIL or UNCHECKED refuses the whole connect. Fix the description, split the file, or drop it, then rerun.
Verdicts are written next to CONNECT.json as <name>.verdicts.json with each file's sha256, so a later run
can tell which files changed since they were last checked.
"""
import hashlib, json, re, subprocess, sys, time, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from reviewed_view import derive
from judge_profile import PROFILE
CEILING_MSG = f"exceeds {PROFILE.ceiling_text}"  # the gate's refusal text (the judge's ask)


# The gate's c1 line can carry any verdict token the reply kit's claim question
# supports: SUPPORTED, NOT_SUPPORTED, or CONTRADICTED. The old regex only matched
# the first two, so a CONTRADICTED verdict fell through to "no match" and was
# reported as ERROR instead of a real FAIL with its own confidence.
KNOWN_VERDICTS = ("SUPPORTED", "NOT_SUPPORTED", "CONTRADICTED")


class PaymentRequired(RuntimeError):
    """The judge refused for payment (HTTP 402, e.g. an empty balance). Every retry would be refused
    too, so callers stop the run rather than split, retry or fall back per file."""


# Claim text rides only on the echoed "$ ..." command line and the verdict rows (superjev.py `_one_line` flattens
# claims to one line, so a claim cannot spill onto other lines); everything else is the door's own output.
_CLAIM_LINE = re.compile(r"^(?:\$ |\s*c\d+\s+\S+\s+[\d.]+)")


def _raise_if_unpaid(txt: str) -> None:
    own = "\n".join(l for l in txt.splitlines() if not _CLAIM_LINE.match(l))
    if re.search(r"\bHTTP 402\b", own):
        raise PaymentRequired("the judge refused the call for payment (HTTP 402); top up, then refresh again")


def gate(description: str, path: str) -> dict:
    t = time.time()
    try:
        r = subprocess.run([sys.executable, str(HERE / "dispatch.py"), "check", "--claim", description, path],
                           capture_output=True, text=True)
    except ValueError as e:  # e.g. a null byte in the description; one bad file must not stop the rest
        return {"state": "ERROR", "reason": f"cannot check this file: {e}", "secs": round(time.time() - t, 1)}
    txt = r.stdout + "\n" + r.stderr
    _raise_if_unpaid(txt)
    secs = round(time.time() - t, 1)
    if CEILING_MSG in txt:
        return {"state": "UNCHECKED", "reason": f"over {PROFILE.window_tokens // 1000}k-token ceiling; split the file", "secs": secs}
    m = re.search(r"\bc1\s+(\S+)\s+([\d.]+)", txt)
    if not m or m.group(1) not in KNOWN_VERDICTS:
        tail = txt.strip().splitlines()[-1:] or ["no output"]
        token = f" (c1 token: {m.group(1)})" if m else ""
        return {"state": "ERROR", "reason": (tail[0][:160] + token)[:200], "secs": secs}
    return {"state": m.group(1), "confidence": float(m.group(2)), "secs": secs}


def gate_many(claims: list, path: str):
    """Several claims against one evidence file in ONE judge call. Returns one verdict dict per
    claim, in order (same shape as gate()), or None when the call failed or any claim row is
    missing -- the caller then falls back to gate() per claim."""
    t = time.time()
    args = [sys.executable, str(HERE / "dispatch.py"), "check"]
    for c in claims:
        args += ["--claim", c]
    try:
        r = subprocess.run([*args, path], capture_output=True, text=True)
    except ValueError:
        return None
    txt = r.stdout + "\n" + r.stderr
    _raise_if_unpaid(txt)
    secs = round(time.time() - t, 1)
    rows = {int(n): (v, float(c)) for n, v, c in re.findall(r"\bc(\d+)\s+(\S+)\s+([\d.]+)", txt)}
    out = []
    for i in range(1, len(claims) + 1):
        if i not in rows or rows[i][0] not in KNOWN_VERDICTS:
            return None
        out.append({"state": rows[i][0], "confidence": rows[i][1], "secs": secs})
    return out


def memory(req: dict) -> dict:
    r = subprocess.run([sys.executable, str(HERE / "dispatch.py"), "memory", "--input", "/dev/stdin"],
                       input=json.dumps(req), capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"status": "error", "raw": (r.stdout + r.stderr)[-400:]}


def watched_refusals(pointer: str, principals: list, paths: list) -> tuple:
    """(the paths the watched rule would refuse for this pointer, why). The rule lives in the engine, which also
    enforces it on every connect and register; prepare_bulk asks it first (action watched-check) so nothing is drafted
    or judged for a file that will be refused. Decides nothing itself."""
    if not principals or not paths:
        return [], ""
    got = memory({"action": "watched-check", "pointer": pointer, "principals": list(principals),
                  "paths": [str(p) for p in paths]})
    if got.get("status") == "ok" and isinstance(got.get("refused"), list):
        return [r["path"] for r in got["refused"] if isinstance(r, dict) and r.get("path")], got.get("message", "")
    return [], ""  # the engine could not answer (or has no such rule): it still decides at the connect itself


def main() -> int:
    from prepare_bulk import has_secret
    args = sys.argv[1:]
    if not args:
        print(__doc__); return 2
    req_path = Path(args[0]).resolve()
    check_only = "--check-only" in args
    line = PROFILE.confidence_line
    if "--line" in args:
        try:
            line = float(args[args.index("--line") + 1])
        except (IndexError, ValueError):
            print(f"--line needs a number, such as --line 0.80\n\n{__doc__}"); return 2
    req = json.loads(req_path.read_text())
    sources = req.get("sources", [])
    if not sources or any(not s.get("description", "").strip() for s in sources):
        print("REFUSED: every source needs a non-empty description"); return 2

    if any("viewTransform" not in s and any(k in s for k in ("viewSHA", "transformSHA")) for s in sources):
        print("REFUSED: view review hashes require their viewTransform policy")
        return 2
    verdicts, failures = [], []
    checked_hashes = {}
    for s in sources:
        p = Path(s["path"]).expanduser().absolute()
        if not p.is_file():
            v = {"state": "ERROR", "reason": "file not found", "secs": 0}
        else:
            raw = p.read_bytes()
            binding = {"sha256": hashlib.sha256(raw).hexdigest()}
            if "viewTransform" in s:
                try:
                    view, policy_sha = derive(raw.decode("utf-8"), s["viewTransform"])
                    if derive(s["description"], s["viewTransform"])[0].decode() != s["description"]:
                        raise ValueError("description requires redaction")
                    if not view.strip() or has_secret(view.decode("utf-8")) or has_secret(s["description"]):
                        raise ValueError("empty or secret-bearing view")
                    # Never hand the original to the description gate.
                    with tempfile.TemporaryDirectory(prefix="superjev-view-") as folder:
                        evidence = Path(folder) / "reviewed.txt"
                        evidence.write_bytes(view)
                        evidence.chmod(0o600)
                        v = gate(s["description"], str(evidence))
                    binding.update(viewSHA=hashlib.sha256(view).hexdigest(), transformSHA=policy_sha)
                except (ValueError, TypeError, UnicodeError):
                    v = {"state": "ERROR", "reason": "invalid view transform", "secs": 0}
            else:
                v = gate(s["description"], str(p))
            checked_hashes[str(p)] = binding
        v["path"] = str(p)
        v.update(checked_hashes.get(str(p), {"sha256": None}))
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
    hashes = {x["path"]: x for x in preview["sources"]}
    for s in req["sources"]:
        path = str(Path(s["path"]).expanduser().absolute())
        checked = checked_hashes[path]
        if any(hashes.get(path, {}).get(k) != v for k, v in checked.items()):
            print("REFUSED: source or view changed after its description check")
            return 1
        s.update(checked)
    if "navigationSHA" in preview:
        req["navigationSHA"] = preview["navigationSHA"]
    req["reviewed"] = True
    reg = memory(req)
    if reg.get("reason") == "watched-refused":
        print("REFUSED:", reg.get("message"))
    print("connect:", reg.get("status"), "pointer:", reg.get("pointer"), "sources:", len(reg.get("sources", [])))
    for warning in reg.get("cleanupWarnings", []):
        print("retention review:", warning)
    return 0 if reg.get("status") == "registered" else 1


if __name__ == "__main__":
    sys.exit(main())
