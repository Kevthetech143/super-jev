"""The context setting: how much of each note one content check reads, as a person picks it.

A size is a name: small, medium or large. judge_profiles.json says what each one reads (its `contexts` table, and
ask.read_budget); this module says only which size an ask uses, and where that choice is kept. The first of these that is
set is the one used:

    --context NAME     one ask: `ask.py --principal P --context large "question"`
    SUPERJEV_CONTEXT   the shell's choice, for every ask it runs. Only ask.py reads it: the size reaches the content check
                       as a `context` key in that check's own input, so routing and every other judge call stay small
    settings.json      the saved choice, `ask.py --principal P --set-context NAME`: the `context` key of the file in the
                       principal's state folder (written atomically, the file's other keys kept)
    default_context    the judge profile's own, small

A name is read as a person types it (trimmed, any case). One that is not small, medium or large, or one the judge profile
states no size for, is a ValueError whose message is the line ask.py prints before it exits 2. Only the source that is
used is read, so a bad value in a source that something stronger overrides is never looked at.
"""
import json
import os
from pathlib import Path

import judge_profile

#: The sizes a person can pick, smallest first. The judge profile says which of them it states a size for.
NAMES = ("small", "medium", "large")
#: The environment variable and the file (in the principal's state folder) the choice can come from.
ENV = "SUPERJEV_CONTEXT"
FILE = "settings.json"
#: Where the size in use came from, as `--status` says it.
WHERE = {"flag": "from --context", "env": f"from {ENV}", "settings": f"from {FILE}", "default": "the default"}


def pick(value, source: str = "flag") -> str:
    """The size `value` names, lower-case. A ValueError, in one line, when it is not one of NAMES (naming where it came from
    when that is not the flag just typed) or when the judge profile states no size for it (the profile's own error, which
    names the sizes it does state)."""
    name = value.strip().lower() if isinstance(value, str) else None
    if name not in NAMES:
        where = {"env": f" in {ENV}", "settings": f" in {FILE}"}.get(source, "")
        raise ValueError(f"unknown context {json.dumps(value, ensure_ascii=False)}{where} (choose {', '.join(NAMES[:-1])} or {NAMES[-1]})")
    judge_profile.load(judge_profile.PROFILE.name, context=name)
    return name


def saved(sdir):
    """The choice saved in <sdir>/settings.json, as written, or None: no file, one that cannot be read or is not an object,
    or one with no `context` in it. Whether it is a size is for pick to say."""
    try:
        return json.loads((Path(sdir) / FILE).read_text()).get("context")
    except (OSError, ValueError, AttributeError):
        return None


def resolve(flag, sdir):
    """(size, where it came from) for one ask: the flag if one was given, else SUPERJEV_CONTEXT if it is not blank, else the
    saved choice, else the judge profile's default_context. ValueError when the one used is not a size that can be picked."""
    env = os.environ.get(ENV, "").strip()
    if flag is not None:
        return pick(flag), "flag"
    if env:
        return pick(env, "env"), "env"
    if (value := saved(sdir)) is not None:
        return pick(value, "settings"), "settings"
    return judge_profile.PROFILE.default_context, "default"


def save(sdir, name: str) -> Path:
    """Save `name` (already picked) as the `context` of <sdir>/settings.json and return that path. The file is written whole
    beside itself and moved over the old one, so a reader sees the old choice or the new, never half of one, and a write that
    fails leaves the old file and no stray one. The file's other keys stay; one that cannot be read is replaced. OSError
    when it cannot be written."""
    path = Path(sdir) / FILE
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = None
    data = {**data, "context": name} if isinstance(data, dict) else {"context": name}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=1) + "\n")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def split_flag(args: list):
    """(args without `--context NAME`, NAME or None). The flag may stand anywhere before the first `--`, and the last one
    wins; after a `--` every word is the question's, flag or not. A flag with no word after it before the `--` gives the
    name "", which pick refuses like any other name that is not one."""
    stop = args.index("--") if "--" in args else len(args)
    rest, flag, i = [], None, 0
    while i < stop:
        if args[i] == "--context":
            flag, i = (args[i + 1] if i + 1 < stop else ""), i + 2
        else:
            rest.append(args[i])
            i += 1
    return rest + args[stop:], flag


def status_line(preset: str, source: str) -> str:
    """`Context: Medium (from --context)`: the line --status prints for the size in use."""
    return f"Context: {judge_profile.load(judge_profile.PROFILE.name, context=preset).context_label} ({WHERE[source]})"
