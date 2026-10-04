#!/usr/bin/env python3
"""A/B the word search: origin/main vs this branch on a made-up corpus. Exits 0 only if the ranked
scores (rounded to 3) and the order are byte-identical for every question. Local, no network.

    python3 scripts/ab_word_rank.py --questions 30

Also the shared worker for word_search_timing.py: `--worker <skill dir> <state dir> <mode>`.
"""
import argparse
import hashlib
import importlib.util
import inspect
import json
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
TOPICS = ["dentist", "mortgage", "garden", "warranty", "passport", "insurance", "vaccine", "router", "invoice",
          "recipe", "marathon", "lease", "pension", "telescope", "bicycle", "tuition", "orchard", "kayak"]
FILLER = ("the notes say that we should remember to call back about this soon and file the paper "
          "with the other papers in the folder near the window until further notice ").split()
QUESTIONS = [
    "when is the dentist appointment", "what is the mortgage rate", "where is the passport",
    "who handles the car insurance claim", "how do I reset the router password", "what does the warranty cover",
    "which vaccine shots are due", "marathon training schedule", "lease renewal date", "pension payout amount",
    "telescope mirror cleaning", "bicycle tire pressure", "tuition payment deadline", "orchard apple harvest",
    "kayak rental phone number", "recipe for lentil soup", "invoice number for the garden work",
    "reads the source parent folder", "step by step router setup", "papa dentista cita", "dental appointment typo dentst",
    "what is due next month", "garden warranty", "mortgage insurance pension", "phone number of the dentist",
    "who owns the lease", "passport renewal fee", "vaccine records for the marathon", "kayak and bicycle storage",
    "tuition invoice lease",
]


def build_corpus(root: Path, nfiles: int, size: int, seed: int = 7) -> dict:
    """Made-up notes. Returns {path: cache entry} for the prepare cache. A few files are edited after the
    cache is written (stale sha) so the edited-file path is covered too."""
    rng = random.Random(seed)
    (root / "notes").mkdir(parents=True, exist_ok=True)
    cache = {}
    for i in range(nfiles):
        topic = TOPICS[i % len(TOPICS)]
        body = [f"# Note {i} about {topic}\n"]
        while sum(map(len, body)) < size:
            line = " ".join(rng.choice(FILLER) if rng.random() < 0.8 else rng.choice(TOPICS) for _ in range(14))
            if rng.random() < 0.05:
                line += " call 212-555-%04d" % rng.randrange(10000)
            body.append(line + "\n")
        if i % 11 == 0:
            body.append("Papá visita el dentista, cita café.\n")
        f = root / "notes" / f"{topic}-{i}.md"
        f.write_text("".join(body))
        cache[str(f)] = {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True,
                         "description": f"Notes on {topic}", "question": f"what about {topic}"}
    for i in range(0, nfiles, 7):  # edited since connect
        f = Path(list(cache)[i])
        f.write_text(f.read_text() + "\nExtra line about the kayak lease and the dentist.\n")
    return cache


def worker(skill: str, state: str, mode: str, nfiles: int, size: int, nq: int) -> None:
    """Run in a fresh process: load that tree's ask.py, search, print JSON."""
    sys.path.insert(0, skill)
    spec = importlib.util.spec_from_file_location("ask_ab", Path(skill) / "ask.py")
    ask = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ask)
    root = Path(state)
    cdir = root / "prepare-cache"
    cdir.mkdir(parents=True, exist_ok=True)
    if not (cdir / "p1.json").exists():
        cache = build_corpus(root, nfiles, size)
        (cdir / "p1.json").write_text(json.dumps(cache))
        (cdir / "p1-report.json").write_text(json.dumps({"pointer": "p1", "principals": ["me"],
                                                         "roots": [str(root / "notes")]}))
    ask.prepare_bulk.CACHE_DIR = cdir
    kw = {}
    if "index_path" in inspect.signature(ask.word_search).parameters:
        kw["index_path"] = root / "idx" / "word-index.json"
    out, times = [], []
    for q in QUESTIONS[:nq]:
        t0 = time.perf_counter()
        r = ask.word_search(q, ["p1"], limit=50, **kw)
        times.append(time.perf_counter() - t0)
        out.append([[round(s, 3), Path(p).name, ptr] for s, p, ptr in r])
    print(json.dumps({"ranks": out, "times": times}))


def run_tree(skill: Path, state: Path, mode: str, nfiles: int, size: int, nq: int) -> dict:
    p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(skill), str(state), mode,
                        str(nfiles), str(size), str(nq)], capture_output=True, text=True)
    if p.returncode:
        sys.exit(f"worker failed: {p.stderr[-800:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def main_tree(tmp: Path) -> Path:
    """origin/main's skill dir, exported from git."""
    subprocess.run(["git", "fetch", "-q", "origin", "main"], check=False)
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
    tar = subprocess.run(["git", "-C", top, "archive", "origin/main", "skills/super-jev"], capture_output=True, check=True).stdout
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=tar, check=True)
    return tmp / "skills" / "super-jev"


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6]), int(sys.argv[7]))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=int, default=30)
    ap.add_argument("--files", type=int, default=60)
    a = ap.parse_args()
    nq = min(a.questions, len(QUESTIONS))
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        base = main_tree(t / "main")
        # Same corpus for both: built once by the first worker into its own dir, copied by path rebuild (seeded).
        ra = run_tree(base, t / "a", "base", a.files, 6000, nq)
        rb = run_tree(SKILL, t / "b", "new", a.files, 6000, nq)
        rb2 = run_tree(SKILL, t / "b", "new-warm", a.files, 6000, nq)  # second run reads the saved index
    bad = [i for i in range(nq) if json.dumps(ra["ranks"][i]) != json.dumps(rb["ranks"][i])
           or json.dumps(ra["ranks"][i]) != json.dumps(rb2["ranks"][i])]
    empty = sum(1 for r in ra["ranks"] if not r)
    print(f"questions={nq} identical={nq - len(bad)} differing={bad} questions_with_hits={nq - empty}")
    return 0 if not bad and nq - empty >= nq // 2 else 1


if __name__ == "__main__":
    sys.exit(main())
