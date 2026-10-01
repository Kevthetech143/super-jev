#!/usr/bin/env python3
"""Judge support: does this judge, on this build, meet YOUR bar on YOUR cases? Plus the fingerprint a
result is tied to.

    judge_support.py --ask BUILD/ask.py --judge NAME --cases FILE --cases-sha256 SHA \\
        --bar BAR.json --bar-sha256 SHA --record OUT.json --max-asks N \\
        [--env NAME=VALUE ...] [--time-cap-min M] [--timeout S] [--seed K]
    judge_support.py --fingerprint --ask BUILD/ask.py --judge NAME [--env NAME=VALUE ...]
    judge_support.py --applies RECORD --ask BUILD/ask.py --judge NAME --cases FILE --cases-sha256 SHA \\
        --bar BAR.json --bar-sha256 SHA [--env NAME=VALUE ...]

--applies is free (no ask, no judge call): exit 0 when RECORD is a finished record made on this build, these
cases and this bar (same fingerprint, cases_sha256 and bar_sha256), printing the verdict re-derived from its
stored hits (the stored verdict field is never trusted); else exit 1 with the reason (unreadable, incomplete,
no fingerprint, unknown judge, or fingerprint, cases or bar differ); exit 2 is bad input (a SHA mismatch).

The run asks every case of the file once through the build's full paid ask path, on paid_replay's frozen
copy of state and memory (fresh copy per ask, SUPERJEV_REPLAY=1), times each ask, grades it, and holds the
totals against the bar. It prints PASS, FAIL or OPEN (a stop before the cases run settle it) per bar row
with hits/n, then the verdict, and writes one JSON record (refusing to overwrite one). Exit 0 supported,
1 not supported, 3 incomplete (a time cap, a prepare-cache change mid-run, more excluded cases than the bar
allows, a pointer not ready, an interrupt), 2 bad input (a SHA mismatch prints the actual SHA). See
references/judge-support.md for the file formats.

Cases are paid_replay's (question, gold, principal, split; a question with "absent": true and "gold": [], a
claim with "kind": "claim" and "expected" TRUE, FALSE or ABSENT). The whole file runs; every row names its
principal. The child ask sees no inherited SUPERJEV_* setting: the tool sets SUPERJEV_JUDGE=NAME and the
replay settings, and anything else it needs comes from --env (the NAME goes in the record, the value only
into the fingerprint). The tool never reads, prints or writes a key: the build's own ask.py loads it.

The fingerprint is a SHA-256 over five parts, so it changes exactly when the judge, its profile, an explicit
setting or the engine code that talks to the judge changes, and at no other time:

  judge              the profile key after aliases
  implementation     "profile", or "fake" for the test judge (it uses the default profile's numbers,
                     so a fake run can never stand in for the default judge)
  profile_sha256     that profile's entry in the build's own judge_profiles.json, as canonical JSON,
                     without keys starting "_" (comments and sources)
  judge_path_sha256  the build's code files (.py .ts .mjs .js .sh) under skills/super-jev/ (not its
                     tests/), src/ and experiments/verified-pointer-memory/, plus secret_patterns.json,
                     which decides what may reach the judge. Docs, the changelog, tests and prepared
                     sets (prepare-cache) are not part of it
  env_sha256         the sorted --env NAME=VALUE pairs (the values can hold a token; they are hashed,
                     never printed)

A hosted model alias can change behind the same profile; the fingerprint cannot see that.
"""
import argparse
import hashlib
import json
import os
import random
import re
import sys
import tempfile
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paid_replay as pr  # noqa: E402
import scorecard as sc  # noqa: E402

#: Where a build keeps what decides what the judge is asked and how its answer is read.
JUDGE_PATH = ("skills/super-jev", "src", "experiments/verified-pointer-memory")
CODE_SUFFIXES = (".py", ".ts", ".mjs", ".js", ".sh")
NOT_SHIPPED = ("node_modules", "__pycache__", ".git")
ENV_PAIR = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
#: Every bar row is one of these. (kind, the cases it measures). A "rate" row needs a minimum share of hits,
#: a "count" row allows at most so many bad cases (0 = zero tolerance), a "secs" row bounds a nearest-rank
#: percentile of the asks' wall seconds.
MEASURES = {
    "top5": ("rate", "answerable"),            # a gold file in the final top 5
    "rank1": ("rate", "answerable"),           # a gold file at rank 1
    "absent": ("rate", "absent"),              # no file returned on a not-in-files question
    "claims_right": ("rate", "claim"),         # a TRUE or FALSE claim asserted correctly
    "claims_wrong": ("count", "claim"),        # a TRUE or FALSE claim asserted wrong
    "claims_absent_asserted": ("count", "claim_absent"),  # an ABSENT claim asserted TRUE or FALSE
    "secs": ("secs", "all"),
}
BAR_KEYS = {"rate": "min_rate", "count": "max_count", "secs": None}
PROTECTED_ENV = ("SUPERJEV_JUDGE", "SUPERJEV_STATE_DIR")
ROW_MARK = {True: "PASS", False: "FAIL", None: "OPEN"}  # OPEN: the run stopped before the cases run settled the row


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _judge_file(sub: str, rel: Path) -> bool:
    if any(part in NOT_SHIPPED for part in rel.parts):
        return False
    if sub == JUDGE_PATH[0] and rel.parts[0] == "tests":
        return False
    return rel.suffix in CODE_SUFFIXES or rel.name == "secret_patterns.json"


def judge_path_sha(root: Path) -> str:
    """The build's judge-path files (see the module docstring), names and contents."""
    return _sha({sub: pr.tree_sha(root / sub, lambda rel, sub=sub: _judge_file(sub, rel)) for sub in JUDGE_PATH})


def parse_env(pairs) -> dict:
    """--env NAME=VALUE pairs as {NAME: VALUE}; a malformed or repeated NAME, or one the tool sets itself
    (SUPERJEV_JUDGE and the replay settings), is a ValueError. Every command that reads --env comes here."""
    env = {}
    for pair in pairs or []:
        m = ENV_PAIR.match(pair)
        if not m:
            raise ValueError(f"--env takes NAME=VALUE, got a value with no NAME=: {pair.split('=')[0]!r}")
        if m.group(1) in env:
            raise ValueError(f"--env {m.group(1)} is given twice")
        if m.group(1) in PROTECTED_ENV or m.group(1) in pr.REPLAY_ENV:
            raise ValueError(f"--env {m.group(1)}: the tool sets this one itself")
        env[m.group(1)] = m.group(2)
    return env


def fingerprint(ask: Path, judge: str, env=None) -> dict:
    """The five parts and their combined SHA-256, under "fingerprint", for `judge` on the build `ask`.
    ValueError: an unknown judge, or a build or judge table that cannot be read."""
    import judge_profile as jp  # late: it reads SUPERJEV_JUDGE on import, and this tool never judges
    code = sc.build_path(Path(ask).expanduser().resolve())
    table = code.parent / "judge_profiles.json"
    try:
        default, profiles = jp._read(table)
    except SystemExit as e:
        raise ValueError(str(e)) from None
    key = jp._resolve(judge, default, profiles)  # ValueError names the known judges
    entry = {k: v for k, v in profiles[key].items() if not k.startswith("_")}
    parts = {
        "judge": key,
        "implementation": "fake" if judge == jp.FAKE_JUDGE else "profile",
        "profile_sha256": _sha(entry),
        "judge_path_sha256": judge_path_sha(code.parent.resolve().parents[1]),
        "env_sha256": hashlib.sha256("\n".join(f"{k}={v}" for k, v in sorted((env or {}).items())).encode()).hexdigest(),
    }
    return {**parts, "fingerprint": _sha(parts)}


def stratum(case: dict) -> str:
    if case.get("kind") == "claim":
        return "claim_absent" if case["expected"] == "ABSENT" else "claim"
    return "absent" if sc.is_absent(case) else "answerable"


def in_row(row: dict, case: dict) -> bool:
    """The one membership rule: this case is one the bar row measures."""
    return row["stratum"] in ("all", stratum(case))


def need(hundredths: int, n: int) -> int:
    """The smallest hit count at or above rate x n, in integers (a float ceil gives 8, not 7, for 0.28 x 25)."""
    return -(-(hundredths * n) // 100)


def _hundredths(x, what: str) -> int:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError(f"bar {what}: a rate is a number")
    h = Decimal(str(x)) * 100
    if h != h.to_integral_value():
        raise ValueError(f"bar {what}: a rate has at most two decimals, got {x}")
    if not 1 <= int(h) <= 100:
        raise ValueError(f"bar {what}: a rate is above 0 and at most 1, got {x}")
    return int(h)


def check_sha(flag: str, path, sha) -> bytes:
    """The file's bytes, after its SHA-256 matched the pin. ValueError names the actual SHA."""
    data = Path(path).expanduser().read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError(f"{flag} sha256 mismatch: the file is {hashlib.sha256(data).hexdigest()}")
    return data


def load_bar(path, sha) -> dict:
    """The bar file, after its SHA-256 matched: {"max_excluded": N, "rows": [row, ...]}; each row names its
    measure, its bar and its stratum, and (rate and secs rows) its hundredths. ValueError: anything else."""
    bar = json.loads(check_sha("bar", path, sha))
    if not isinstance(bar, dict) or set(bar) != {"max_excluded", "rows"}:
        raise ValueError('a bar is {"max_excluded": N, "rows": [...]} and nothing else; there are no defaults')
    if not isinstance(bar["max_excluded"], int) or isinstance(bar["max_excluded"], bool) or bar["max_excluded"] < 0:
        raise ValueError("bar max_excluded must be a whole number, 0 or more")
    if not isinstance(bar["rows"], list) or not bar["rows"]:
        raise ValueError("a bar needs at least one row")
    rows, names = [], set()
    for raw in bar["rows"]:
        measure = raw.get("measure") if isinstance(raw, dict) else None
        if measure not in MEASURES:
            raise ValueError(f"bar row measure must be one of {', '.join(MEASURES)}: {raw!r}")
        kind, st = MEASURES[measure]
        if kind == "secs":
            keys = {"measure", "percentile", "max_secs"}
            p, m = raw.get("percentile"), raw.get("max_secs")
            if (not isinstance(p, int) or isinstance(p, bool) or not 1 <= p <= 100 or isinstance(m, bool)
                    or not isinstance(m, (int, float)) or m <= 0):
                raise ValueError(f"bar row {raw!r}: secs needs a percentile (1-100, whole) and max_secs above 0")
            row = {"name": f"secs_p{p}", "bar": {"percentile": p, "max_secs": m}, "h": p}
        else:
            keys = {"measure", BAR_KEYS[kind]}
            value = raw.get(BAR_KEYS[kind])
            if kind == "rate":
                row = {"name": measure, "bar": {"min_rate": value}, "h": _hundredths(value, measure)}
            elif not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"bar row {raw!r}: max_count is a whole number, 0 or more")
            else:
                row = {"name": measure, "bar": {"max_count": value}}
        if set(raw) != keys:
            raise ValueError(f"bar row {raw!r}: expected exactly {sorted(keys)}")
        if row["name"] in names:
            raise ValueError(f"bar row {row['name']} is given twice")
        names.add(row["name"])
        rows.append({**row, "measure": measure, "kind": kind, "stratum": st})
    return {"max_excluded": bar["max_excluded"], "rows": rows}


def row_state(row: dict, hits: int, n: int, left: int = 0):
    """The one grading rule for a bar row: `n` cases have been graded and `left` have not run. True: met, whatever
    the cases not yet run do. False: out of reach, or nothing measured (a row that measured nothing never passes).
    None: still open. The bar's needs are worked out over every case the row will see, n + left."""
    n += left
    if n == 0:
        return False
    if row["kind"] == "count":
        return False if hits > row["bar"]["max_count"] else (None if left else True)
    short = need(row["h"], n)
    return True if hits >= short else (False if hits + left < short else None)


def row_passes(row: dict, hits: int, n: int) -> bool:
    """A finished row (nothing left to run) meets its bar."""
    return row_state(row, hits, n) is True


def finished(stop) -> bool:
    """A run that ended complete, or because a row's bar was out of reach, counts; any other stop is incomplete."""
    return isinstance(stop, str) and (stop == "complete" or stop.startswith("bar-unreachable"))


def verdict_of(met: bool, stop: str) -> str:
    """The one verdict rule, for a run and for a stored record: supported needs a complete run with every row met."""
    return ("supported" if met and stop == "complete" else "not supported") if finished(stop) else "incomplete"


def row_score(row: dict, case: dict, res: dict):
    """None when the case is not in this row's stratum; else 1 or 0: a hit, or for a count row a bad case.
    An errored case never hits; an errored question is over every time line; an errored claim is held,
    never asserted (it fails 'claims_right' only)."""
    if not in_row(row, case):
        return None
    err, m = "error" in res, row["measure"]
    if m == "top5":
        return int(not err and res["rank"] is not None)
    if m == "rank1":
        return int(not err and res["rank"] == 1)
    if m in ("absent", "claims_right"):
        return int(not err and res["ok"])
    if m in ("claims_wrong", "claims_absent_asserted"):
        asserted = not err and res.get("verdict") in ("TRUE", "FALSE")
        return int(asserted and (m == "claims_absent_asserted" or res["verdict"] != case["expected"]))
    return int(res["secs"] <= row["bar"]["max_secs"] and not (err and case.get("kind") != "claim"))


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run(a, ap) -> int:
    """One judge, one build, the whole case file, one record. Returns the exit code."""
    try:
        check_sha("cases", a.cases, a.cases_sha256)
        bar = load_bar(a.bar, a.bar_sha256)
        env = parse_env(a.env)
        if Path(a.record).expanduser().exists():
            raise ValueError(f"{a.record} exists; a record is never overwritten")
        plan = pr.load_run([a.ask], [a.cases], None, a.max_asks, memory_hint="")
        for c in plan.cases:
            try:
                sc.principal_name(c["principal"])
            except Exception as e:  # argparse.ArgumentTypeError from the shared name rule
                raise ValueError(str(e)) from None
        fp = fingerprint(Path(a.ask), a.judge, env)
        for row in bar["rows"]:
            if not any(in_row(row, c) for c in plan.cases):
                raise ValueError(f"bar row {row['name']} measures {row['stratum']} cases and the file has none")
    except (OSError, ValueError, TypeError, AttributeError) as e:
        ap.error(str(e))
    build, cases, cache = plan.builds[0], plan.cases, plan.caches[0]
    child = {k: v for k, v in os.environ.items() if not k.startswith("SUPERJEV_")}
    child.update(env, SUPERJEV_JUDGE=a.judge)
    missing = [c for c in cases if c["gold"] and not any(os.path.exists(g) for g in c["gold"])]
    runnable = [c for c in cases if c not in missing]
    random.Random(a.seed).shuffle(runnable)
    rec = {"judge": fp["judge"], "implementation": fp["implementation"], "fingerprint": fp["fingerprint"],
           "fingerprint_parts": {k: fp[k] for k in ("profile_sha256", "judge_path_sha256", "env_sha256")},
           "env_names": sorted(env), "ask": str(build), "cases_sha256": a.cases_sha256, "bar_sha256": a.bar_sha256,
           "max_excluded": bar["max_excluded"], "seed": a.seed, "start": _now()}
    acc = {r["name"]: {"hits": 0, "n": 0, "seen": 0, "split": {}} for r in bar["rows"]}
    total = {r["name"]: sum(in_row(r, c) for c in runnable) for r in bar["rows"]}
    graded, excluded, asks, stop, t0 = [], {"missing_gold": [c["question"] for c in missing], "drift": []}, 0, "", time.monotonic()
    snap, errors = {}, 0

    def unreachable(row):
        r = acc[row["name"]]
        return row_state(row, r["hits"], r["n"], total[row["name"]] - r["seen"]) is False

    with tempfile.TemporaryDirectory(prefix="sj-support-base-") as base:
        base = Path(base)
        try:
            snap = pr.snapshot({c["principal"] for c in cases}, plan.config, base)
        except (OSError, ValueError, KeyError, pr.sqlite3.Error) as e:
            ap.error(f"cannot snapshot state and memory: {e}")
        rec["state_root"], rec["snapshot"] = str(pr.state_root()), snap
        if len(missing) > bar["max_excluded"]:
            stop = "excluded over max_excluded"
        elif why := pr.preflight([build], base, {c["principal"] for c in cases}, a.timeout, environ=child):
            stop = "could-not-start"
            rec["not_ready"] = why
        else:
            sources = pr.eligible_sources(base, cache)
            try:
                for i, c in enumerate(runnable):
                    gold = {os.path.realpath(g) for g in c["gold"]}
                    before = {p: pr.file_sha(p) for p in sources | gold}
                    res = pr.grade(build, base, c, a.timeout, environ=child)
                    asks += 1
                    why = pr.drifted({"read": {}}, res, before) if "error" not in res else ""
                    members = [r for r in bar["rows"] if in_row(r, c)]
                    for r in members:
                        acc[r["name"]]["seen"] += 1
                    entry = {"question": c["question"], "principal": c["principal"], "split": c["split"],
                             "kind": c.get("kind") or "question", "stratum": stratum(c),
                             **{k: v for k, v in res.items() if k != "read"}}
                    if why:
                        excluded["drift"].append({"question": c["question"], "why": why})
                        entry["excluded"] = "drift"
                    else:
                        errors += "error" in res
                        for r in members:
                            score, a_ = row_score(r, c, res), acc[r["name"]]
                            a_["hits"] += score
                            a_["n"] += 1
                            sp = a_["split"].setdefault(c["split"], {"hits": 0, "n": 0})
                            sp["hits"] += score
                            sp["n"] += 1
                    graded.append(entry)
                    if len(excluded["missing_gold"]) + len(excluded["drift"]) > bar["max_excluded"]:
                        stop = "excluded over max_excluded"
                    elif out_of_reach := [r["name"] for r in bar["rows"] if unreachable(r)]:
                        stop = "bar-unreachable: " + ", ".join(out_of_reach)
                    elif a.time_cap_min is not None and i + 1 < len(runnable) and time.monotonic() - t0 > a.time_cap_min * 60:
                        stop = "time cap"
                    if stop:
                        break
            except KeyboardInterrupt:
                stop = "interrupted"
        if asks and pr.tree_sha(cache) != plan.cache_sha:  # the data under every ask moved: no result stands
            stop = "prepare-cache changed during the run"
    rows = []
    for r in bar["rows"]:
        x = acc[r["name"]]
        left = total[r["name"]] - x["seen"]  # cases never run (0 after a complete run); a row's n counts them
        n = x["n"] + left
        rows.append({"name": r["name"], "measure": r["measure"], "kind": r["kind"], "stratum": r["stratum"],
                     "bar": r["bar"], "hits": x["hits"], "n": n, "pass": row_state(r, x["hits"], x["n"], left),
                     **({"need": need(r["h"], n)} if r["kind"] != "count" else {}),
                     **({"not_run": left} if left else {}), "by_split": x["split"]})
    if not stop:
        stop = "complete"
    verdict = verdict_of(all(r["pass"] for r in rows), stop)
    rec.update(end=_now(), rows=rows, excluded=excluded, error_count=errors, asks=asks, n_run=len(graded) - len(excluded["drift"]),
               N=len(runnable), stop_reason=stop, verdict=verdict, cases=graded,
               secs_total=round(time.monotonic() - t0, 1))
    text = json.dumps(rec, indent=1)
    for v in env.values():  # a setting's value can hold a token: never in the record, even inside an error line
        if len(v) >= 6:
            text = text.replace(json.dumps(v)[1:-1], "<env value>")
    out = Path(a.record).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"judge_support: {fp['judge']} ({fp['implementation']}), fingerprint {fp['fingerprint'][:12]}, "
          f"{rec['n_run']}/{rec['N']} case(s) run, {asks} ask(s), {errors} error(s)")
    if rec.get("not_ready"):
        print("  not started, no ask spent: every pointer must be ready (a run never reconnects):\n  " + rec["not_ready"])
    for r in rows:
        shown = (f"{r['hits']} bad" if r["kind"] == "count" else f"{r['hits']}/{r['n']}"
                 ) + (f", {r['not_run']} not run" if "not_run" in r else "")
        print(f"  {ROW_MARK[r['pass']]:5} {r['name']:24} {shown}  (bar: "
              + ", ".join(f"{k} {v}" for k, v in r["bar"].items()) + (f"; needs {r['need']}" if "need" in r else "") + ")")
    if any(excluded.values()):
        print(f"  excluded: {len(excluded['missing_gold'])} missing gold, {len(excluded['drift'])} drift "
              f"(at most {bar['max_excluded']})")
    print(f"verdict: {verdict} ({stop})")
    print(f"record: {out}")
    return {"supported": 0, "not supported": 1, "incomplete": 3}[verdict]


def rederive(rows) -> bool:
    """True when every stored row meets its stored bar, worked out again from the stored hits and n
    (a row that measured nothing never passes). ValueError: a row that is not shaped like a record's."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("no bar rows")
    for r in rows:
        try:
            bar, hits, n = r["bar"], r["hits"], r["n"]
            if r["kind"] == "count":
                row = {"kind": "count", "bar": {"max_count": bar["max_count"]}}
            elif r["kind"] == "rate":
                row = {"kind": "rate", "h": _hundredths(bar["min_rate"], r["name"]), "bar": bar}
            elif r["kind"] == "secs":
                row = {"kind": "secs", "h": bar["percentile"], "bar": bar}
            else:
                raise ValueError(f"row {r['name']}: unknown kind {r['kind']!r}")
            if not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in (hits, n)):
                raise ValueError(f"row {r['name']}: hits and n are whole numbers")
        except (KeyError, TypeError) as e:
            raise ValueError(f"a stored row is malformed ({type(e).__name__}: {e})") from None
        if not row_passes(row, hits, n):
            return False
    return True


def applies(a, ap) -> int:
    """Does this record belong to this build, these cases and this bar? Exit 0 yes (and its verdict), 1 no."""
    try:
        check_sha("cases", a.cases, a.cases_sha256)
        check_sha("bar", a.bar, a.bar_sha256)
        env = parse_env(a.env)
    except (OSError, ValueError) as e:
        ap.error(str(e))

    def no(why):
        print(f"does not apply: {why}")
        return 1

    try:
        rec = json.loads(Path(a.applies).expanduser().read_text())
        if not isinstance(rec, dict):
            raise ValueError("not a record")
    except (OSError, ValueError) as e:
        return no(f"record unreadable ({e})")
    stop = rec.get("stop_reason")
    if not finished(stop):
        return no(f"incomplete ({stop or 'no stop reason'})")
    if not isinstance(rec.get("fingerprint"), str) or not rec["fingerprint"]:
        return no("no fingerprint")
    try:
        now = fingerprint(Path(a.ask), a.judge, env)
    except (OSError, ValueError) as e:
        return no(str(e))  # an unknown judge names the known ones
    for what, mine, theirs in (("fingerprint", now["fingerprint"], rec["fingerprint"]),
                               ("cases", a.cases_sha256, rec.get("cases_sha256")),
                               ("bar", a.bar_sha256, rec.get("bar_sha256"))):
        if mine != theirs:
            return no(f"{what} differs")
    try:
        met = rederive(rec.get("rows"))
    except ValueError as e:
        return no(str(e))
    verdict = verdict_of(met, stop)
    print(f"applies: {verdict} (re-derived from the stored hits; ended {rec.get('end')}, "
          f"fingerprint {rec['fingerprint'][:12]})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Judge support: grade one judge on one build against your bar, "
                                             "print its fingerprint, or say whether a record applies.")
    ap.add_argument("--fingerprint", action="store_true", help="print the fingerprint (free) and stop")
    ap.add_argument("--applies", metavar="RECORD",
                    help="free: exit 0 when RECORD is a finished record for this build, cases and bar, else 1")
    ap.add_argument("--ask", help="the build's ask.py")
    ap.add_argument("--judge", help="the judge name (a profile, an alias or fake)")
    ap.add_argument("--env", action="append", default=[], metavar="NAME=VALUE",
                    help="a setting the run needs; part of the fingerprint (repeatable)")
    ap.add_argument("--cases", help="the case file (JSONL; the whole file runs)")
    ap.add_argument("--cases-sha256", help="its SHA-256")
    ap.add_argument("--bar", help="the bar file (JSON)")
    ap.add_argument("--bar-sha256", help="its SHA-256")
    ap.add_argument("--record", help="where to write the record (never overwritten)")
    ap.add_argument("--max-asks", type=int, help="hard cap on paid asks")
    ap.add_argument("--time-cap-min", type=float, help="stop, incomplete, when the run passes this many minutes")
    ap.add_argument("--timeout", type=float, default=300, help="seconds per ask (default 300)")
    ap.add_argument("--seed", type=int, default=0, help="case order seed (default 0; recorded)")
    a = ap.parse_args(argv)
    os.environ.pop("SUPERJEV_JUDGE", None)  # this tool never judges: a stray selector must not stop it reading the table
    if a.fingerprint and a.applies:
        ap.error("--fingerprint and --applies are separate commands")
    need_flags = ["ask", "judge"] + ([] if a.fingerprint else ["cases", "cases_sha256", "bar", "bar_sha256"]
                                     + ([] if a.applies else ["record", "max_asks"]))
    if absent := [f"--{n.replace('_', '-')}" for n in need_flags if getattr(a, n) is None]:
        ap.error("required: " + ", ".join(absent))
    if a.fingerprint:
        try:
            print(json.dumps(fingerprint(Path(a.ask), a.judge, parse_env(a.env)), sort_keys=True))
        except (OSError, ValueError) as e:
            ap.error(str(e))
        return 0
    return applies(a, ap) if a.applies else run(a, ap)


if __name__ == "__main__":
    sys.exit(main())
