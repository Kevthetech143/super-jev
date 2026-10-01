#!/usr/bin/env python3
"""Which judge on which build: the fingerprint a support record is tied to.

    judge_support.py --fingerprint --ask BUILD/ask.py --judge NAME [--env NAME=VALUE ...]

Prints the build's fingerprint for that judge as one JSON line (free: no ask, no judge call).
The fingerprint is a SHA-256 over five parts, so it changes exactly when the judge, its profile,
an explicit setting or the engine code that talks to the judge changes, and at no other time:

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
Exit 0 prints the fingerprint; exit 2 is bad input (an unknown judge, an unreadable build or table).
"""
import argparse
import hashlib
import json
import os
import re
import sys
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
    """--env NAME=VALUE pairs as {NAME: VALUE}; a malformed or repeated NAME is a ValueError."""
    env = {}
    for pair in pairs or []:
        m = ENV_PAIR.match(pair)
        if not m:
            raise ValueError(f"--env takes NAME=VALUE, got a value with no NAME=: {pair.split('=')[0]!r}")
        if m.group(1) in env:
            raise ValueError(f"--env {m.group(1)} is given twice")
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Judge support: the fingerprint of one judge on one build.")
    ap.add_argument("--fingerprint", action="store_true", required=True, help="print the fingerprint (free)")
    ap.add_argument("--ask", required=True, help="the build's ask.py")
    ap.add_argument("--judge", required=True, help="the judge name (a profile, an alias or fake)")
    ap.add_argument("--env", action="append", default=[], metavar="NAME=VALUE",
                    help="a setting the run needs; part of the fingerprint (repeatable)")
    a = ap.parse_args(argv)
    os.environ.pop("SUPERJEV_JUDGE", None)  # this tool never judges: a stray selector must not stop it reading the table
    try:
        print(json.dumps(fingerprint(Path(a.ask), a.judge, parse_env(a.env)), sort_keys=True))
    except (OSError, ValueError) as e:
        ap.error(str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
