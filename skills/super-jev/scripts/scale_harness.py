#!/usr/bin/env python3
"""Synthetic scale harness (S0): how much LOCAL work does one ask do as the connected corpus grows?

Makes a made-up corpus of notes, docs and code in a temp dir (1x = ~3,300 files, 10x = ~33,000),
connects it into a temp state dir with the built-in writer (no judge call), then times real asks on the
current code. The judge is a STUB: Node's fetch is replaced by an offline function that answers every
request instantly and appends one line to a counter file. No network, no key, no spend, and neither
the live state dir nor the live install is touched (every path lives under the temp dir).

Printed per ask: wall seconds, stub judge calls, and the files opened and hashed. "Opened" counts
Python opens of files under the corpus; "hashed" counts hashlib sha256 digests (any bytes, every Python
process the ask spawns, through a sitecustomize shim). Node-side reads are not counted.

    python3 skills/super-jev/scripts/scale_harness.py --scale 1
    python3 skills/super-jev/scripts/scale_harness.py --scale 10
"""
import argparse
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parents[1]
BASE_FILES = 3300
FILES_PER_POINTER = 150
QUESTIONS = [  # (question, planted file marker word)
    ("which vendor supplies the quartzite countertops for the harbor project", "quartzite"),
    ("what is the retention period for the zephyr audit logs", "zephyr"),
    ("who approved the marigold budget revision", "marigold"),
    ("what port does the tamarind sync daemon listen on", "tamarind"),
    ("when is the obsidian migration freeze", "obsidian"),
]
WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa "
         "quebec romeo sierra tango uniform victor whiskey xray yankee zulu ledger invoice roster backlog "
         "sprint rollout cluster gateway pipeline schedule archive policy review budget vendor ticket").split()

STUB_JS = """
const fs = require('fs');
globalThis.fetch = async (_url, opts) => {
  fs.appendFileSync(process.env.SJ_STUB_COUNT, '1\\n');
  const payload = JSON.parse(opts.body);
  const answers = Object.fromEntries(Object.entries(payload.questions).map(([id, q]) => {
    const keys = Object.keys(q.criteria);
    const choice = keys.find(k => k.endsWith('o_0')) || keys[0];
    return [id, {type: 'choice', choice, confidence: 0.95,
      probabilities: Object.fromEntries(keys.map(k => [k, k === choice ? 0.95 : 0.05 / (keys.length - 1 || 1)]))}];
  }));
  return new Response(JSON.stringify({model: 'offline-stub', answers}), {status: 200});
};
"""

SITECUSTOMIZE = """
import atexit, builtins, hashlib, io, json, os, socket
ROOT = os.environ.get("SJ_COUNT_ROOT", "")
OUT = os.environ.get("SJ_COUNT_OUT")
if ROOT and OUT:
    opened, hashed, net = [], [0], [0]
    _open = io.open
    class counting_open:  # a class, not a function: Python 3.10 pathlib keeps io.open as a class attribute, and a function would bind as a method
        def __call__(self, file, mode="r", *a, **k):
            if isinstance(file, (str, os.PathLike)) and "r" in mode and str(file).startswith(ROOT):
                opened.append(str(file))
            return _open(file, mode, *a, **k)
    io.open = builtins.open = counting_open()
    _sha = hashlib.sha256
    def counting_sha(*a, **k):
        hashed[0] += 1
        return _sha(*a, **k)
    hashlib.sha256 = counting_sha
    _connect = socket.socket.connect
    def no_net(self, address):
        if self.family != getattr(socket, "AF_UNIX", None):
            net[0] += 1
            raise OSError("scale harness: network is blocked")
        return _connect(self, address)
    socket.socket.connect = no_net
    def dump():
        with _open(OUT, "a") as f:
            f.write(json.dumps({"opened": len(opened), "distinct": len(set(opened)), "hashed": hashed[0], "net": net[0]}) + "\\n")
    atexit.register(dump)
"""


def make_corpus(root: Path, files: int, seed: int = 7) -> int:
    """~60% md notes/docs, 20% py, 5% sh, 15% json; a planted answer file per question. Returns file count."""
    r = random.Random(seed)
    pointers = max(1, files // FILES_PER_POINTER)
    kinds = ["md"] * 12 + ["py"] * 4 + ["sh"] + ["json"] * 3
    made = 0
    for i in range(files):
        d = root / f"p{i % pointers:03d}"
        d.mkdir(parents=True, exist_ok=True)
        ext = kinds[i % len(kinds)]
        words = lambda n: " ".join(r.choice(WORDS) + str(r.randint(0, 40)) for _ in range(n))
        if ext == "md":
            body = f"# Note {i}\n\n" + "".join(f"## Section {j}\n{words(r.randint(20, 120))}.\n\n" for j in range(r.randint(1, 6)))
        elif ext == "py":
            body = "".join(f'def fn_{i}_{j}(x):\n    """{words(8)}"""\n    return x + {j}\n\n' for j in range(r.randint(2, 12)))
        elif ext == "sh":
            body = "#!/bin/sh\n" + "".join(f"# {words(6)}\necho step{j}\n" for j in range(r.randint(2, 10)))
        else:
            body = json.dumps({f"k{j}": words(4) for j in range(r.randint(3, 25))}, indent=1)
        (d / f"f{i:05d}.{ext}").write_text(body)
        made += 1
    for n, (q, word) in enumerate(QUESTIONS):  # the answer files, spread over the pointers
        d = root / f"p{(n * 7) % pointers:03d}"
        (d / f"answer-{word}.md").write_text(
            f"# {word.title()} record\n\n{q.capitalize()}: the {word} answer is code {word}-{1000 + n}. "
            f"This note is the only place that says it.\n")
        made += 1
    return made


def connect(root: Path, state: Path, tmp: Path, env: dict) -> float:
    """One prepare_bulk run per top-level folder (one pointer each), built-in writer, into the temp state."""
    sys.path.insert(0, str(SKILL))
    sys.path.insert(0, str(REPO / "experiments" / "verified-pointer-memory"))
    import prepare_bulk as pb
    pb.CACHE_DIR = tmp / "prepare-cache"
    saved, argv = dict(os.environ), sys.argv
    os.environ.update(env)
    started = time.time()
    try:
        for folder in sorted(root.iterdir()):
            sys.argv = ["prepare_bulk.py", "--root", str(folder), "--pointer", folder.name, "--principal", "scale",
                        "--writer", "builtin", "--ext", "py,sh,json", "--max-files", str(2 * FILES_PER_POINTER)]
            devnull = open(os.devnull, "w")
            old = sys.stdout
            sys.stdout = devnull
            try:
                rc = pb.main()
            finally:
                sys.stdout = old
            if rc:
                raise RuntimeError(f"connect of {folder.name} exited {rc}")
    finally:
        sys.argv = argv
        os.environ.clear()
        os.environ.update(saved)
    return time.time() - started


def run(files: int, questions: int = len(QUESTIONS), keep: bool = False, say=print) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="sj-scale-"))
    try:
        corpus, state = tmp / "corpus", tmp / "state"
        (state / "_memory").mkdir(parents=True)
        config = {"db": str(tmp / "db.sqlite"), "registry": str(tmp / "registry.json"),
                  "navigationCommand": ["node", str(REPO / "src" / "navigation-cli.ts")],
                  "retrievalCommand": ["node", str(REPO / "src" / "navigation-cli.ts")],
                  "providerTimeoutSeconds": 60, "cacheTtlSeconds": 60, "reviewTtlSeconds": 60}
        (state / "_memory" / "config.json").write_text(json.dumps(config))
        (tmp / "stub.cjs").write_text(STUB_JS)
        shim = tmp / "shim"
        shim.mkdir()
        (shim / "sitecustomize.py").write_text(SITECUSTOMIZE)
        stub_count, count_out = tmp / "stub-calls.txt", tmp / "counts.jsonl"
        profiles = json.loads((SKILL / "judge_profiles.json").read_text())
        key_env = profiles["profiles"][profiles["default"]]["key_env"]  # the default judge's key name, from its profile
        env = {"SUPERJEV_STATE_DIR": str(state), key_env: "fake-not-real", "SUPERJEV_AUTO_CACHE": "0",
               "SUPERJEV_NEW_FILE_SCAN": "0", "SUPERJEV_SKILLS": "0", "SUPERJEV_REPO": str(REPO),
               "SUPERJEV_SHARED_POINTERS": str(tmp / "no-shared.json"), "XDG_CONFIG_HOME": str(tmp / "xdg"),
               "NODE_OPTIONS": f"--require={tmp / 'stub.cjs'}", "SJ_STUB_COUNT": str(stub_count)}
        t = time.time()
        made = make_corpus(corpus, files)
        say(f"generate: {made} files in {time.time() - t:.1f}s")
        connect_s = connect(corpus, state, tmp, env)
        say(f"connect: {connect_s:.1f}s (not part of an ask)")
        ask_env = {**os.environ, **env, "SJ_COUNT_ROOT": str(corpus), "SJ_COUNT_OUT": str(count_out),
                   "PYTHONPATH": f"{shim}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"}
        ask_env.pop("SUPERJEV_JUDGE", None)
        rows = []
        for q, _word in QUESTIONS[:questions]:
            for p in (stub_count, count_out):
                p.unlink(missing_ok=True)
            t = time.time()
            out = subprocess.run([sys.executable, str(SKILL / "ask.py"), "--principal", "scale", q],
                                 capture_output=True, text=True, env=ask_env, cwd=str(tmp), timeout=3000)
            secs = time.time() - t
            procs = [json.loads(x) for x in count_out.read_text().splitlines()] if count_out.exists() else []
            calls = len(stub_count.read_text().splitlines()) if stub_count.exists() else 0
            rows.append({"question": q, "seconds": round(secs, 2), "exit": out.returncode, "stub_calls": calls,
                         "opened": sum(p["opened"] for p in procs),
                         "hashed": sum(p["hashed"] for p in procs), "network_attempts": sum(p["net"] for p in procs),
                         "processes": len(procs)})
            say(f"ask {len(rows)}: {secs:.2f}s exit={out.returncode} stub_calls={calls} opened={rows[-1]['opened']} "
                f"hashed={rows[-1]['hashed']} procs={len(procs)}")
        return {"files": made, "connect_seconds": round(connect_s, 1), "asks": rows,
                "median_ask_seconds": round(statistics.median(r["seconds"] for r in rows), 2) if rows else None,
                "stub_calls": sum(r["stub_calls"] for r in rows),
                "network_attempts": sum(r["network_attempts"] for r in rows)}
    finally:
        if keep:
            say(f"kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scale", type=int, choices=(1, 10), default=1, help="1 = ~3,300 files, 10 = ~33,000")
    ap.add_argument("--files", type=int, default=None, help="override the file count (smoke runs)")
    ap.add_argument("--questions", type=int, default=len(QUESTIONS))
    ap.add_argument("--keep", action="store_true", help="leave the temp dir in place")
    a = ap.parse_args()
    if subprocess.run(["node", "--version"], capture_output=True).returncode:
        print("scale_harness: node is required for the stub judge", file=sys.stderr)
        return 2
    result = run(a.files or BASE_FILES * a.scale, a.questions, a.keep)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
