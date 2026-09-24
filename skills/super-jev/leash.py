#!/usr/bin/env python3
"""Worker Leash: a PreToolUse hook that keeps a sub-agent's writes inside an
allow-list, and leaves the lead session alone.

See docs/hooks.md for the full writeup (what it does, costs, gaps, install
snippet). Short version:

- Reads a Claude Code PreToolUse hook payload on stdin.
- If the call is not a sub-agent call (no "agent_id" on the payload — the
  field a real lead-session call never carries; verified against a live
  hook_input_log.jsonl capture, see docs/hooks.md), it allows and does
  nothing else. Lead calls are never touched.
- If it IS a sub-agent call, and the tool is Write/Edit/NotebookEdit, the
  target path is checked against an always-deny list first, then an
  allow-list keyed by the agent's id (or type, or "default"). Bash is
  best-effort: a handful of regexes try to find a write target in the
  command string and check it the same way. Anything not covered by a
  regex is invisible to this hook — that is a real gap, not a bug, and it
  is documented as one.
- On deny: exit 2 with a reason on stderr (Claude Code's own hook-block
  convention). On allow: exit 0, silent.
- On any internal error (bad JSON, a crashing regex, anything unexpected):
  allow and log. This hook does NOT fail closed. A crashing PreToolUse
  hook blocks every tool call in the session, for every agent, lead
  included — that is a worse outcome than the leash occasionally missing
  a write it should have caught. See docs/hooks.md "Gaps".

stdlib only. No network, no dependency on the rest of this skill.
"""
import json
import os
import re
import sys
import traceback
from pathlib import Path

# Always-deny defaults. Override/extend with LEASH_DENY (colon-separated
# substrings/globs) or a "always_deny" list in leash.json. These are
# examples of what a real fleet cares about, not universal truths — a
# user-set "reply outbox" path (ours: /tmp/ai-wrapper, where this fleet's
# workflow harness writes an agent's final reply) is the kind of thing
# every install should add its own entry for; document yours, don't rely
# on this file's example matching your layout.
DEFAULT_ALWAYS_DENY = [
    ".env",
    "*secret*",
    "/tmp/ai-wrapper",  # example: this fleet's reply-outbox path
]

WRITE_BASH_PATTERNS = [
    r">>?\s*([^\s|&;]+)",             # redirection: cmd > file / cmd >> file
    r"\btee\s+(?:-a\s+)?([^\s|&;]+)",  # tee [-a] file
    r"\bcp\s+.*?\s([^\s|&;]+)\s*$",   # cp ... dest (best-effort, last token)
    r"\bmv\s+.*?\s([^\s|&;]+)\s*$",   # mv ... dest (best-effort, last token)
    r"\brm\s+(?:-\w+\s+)*([^\s|&;]+)",  # rm [-flags] target
]


# Write sinks that are never a file on disk; `2>/dev/null` must not trip the leash.
HARMLESS_BASH_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}


def _glob_to_regex(pattern):
    """Turn a simple '*'-glob into an anchored regex. No other glob syntax."""
    return "^" + re.escape(pattern).replace(r"\*", ".*") + "$"


def is_subagent_call(data):
    """Sub-agent calls carry an agent_id; lead-session calls do not.

    Verified against a live sandbox capture (hook_input_log.jsonl in the
    original prototype run): every PreToolUse payload for the lead session
    had agent_id absent/None; every sub-agent PreToolUse payload had a
    non-empty agent_id. agent_type is accepted too as a fallback signal
    for callers/harnesses that set it instead.
    """
    return bool(data.get("agent_id") or data.get("agent_type"))


def load_leash_config(config_path):
    if config_path and Path(config_path).exists():
        try:
            return json.loads(Path(config_path).read_text())
        except Exception:
            return {}
    return {}


def get_always_deny(config):
    env_val = os.environ.get("LEASH_DENY")
    if env_val:
        return [p.strip() for p in env_val.split(":") if p.strip()]
    if isinstance(config, dict) and "always_deny" in config:
        return list(config["always_deny"])
    return list(DEFAULT_ALWAYS_DENY)


def get_allowlist(data, config):
    """Resolve the allow-list for this call: LEASH_ALLOW env wins outright,
    else leash.json keyed by agent_id, then agent_type, then "default"."""
    env_list = os.environ.get("LEASH_ALLOW")
    if env_list:
        return [p.strip() for p in env_list.split(":") if p.strip()]
    if not isinstance(config, dict):
        return []
    agent_id = data.get("agent_id")
    agent_type = data.get("agent_type")
    for key in (agent_id, agent_type, "default"):
        if key and key in config:
            val = config[key]
            if isinstance(val, list):
                return val
    return []


def _resolve(path, cwd=None):
    """Normalize a path safely: expand ~, resolve relative-to-cwd and '..'
    (abspath), then resolve symlinks (realpath). macOS's default filesystem
    is case-insensitive, so callers that compare resolved paths should
    casefold them too (see _resolve_ci)."""
    path = os.path.expanduser(path)
    if cwd and not os.path.isabs(path):
        path = os.path.join(os.path.expanduser(cwd), path)
    return os.path.realpath(os.path.abspath(path))


def _resolve_ci(path, cwd=None):
    """_resolve, then casefolded for a case-insensitive compare — macOS's
    default (HFS+/APFS case-insensitive) filesystem treats /Foo and /foo as
    the same path; a case-sensitive compare here would let a worker dodge
    the leash by changing case."""
    return _resolve(path, cwd).casefold()


def path_is_denied_always(path, deny_list, cwd=None):
    norm = _resolve_ci(path, cwd)
    for pat in deny_list:
        pat_norm = pat.casefold()
        if "*" in pat_norm:
            if re.search(_glob_to_regex(pat_norm), norm) or pat_norm.strip("*") in norm:
                return True
        elif pat_norm in norm:
            return True
    return False


def path_is_allowed(path, allowlist, cwd=None):
    if not allowlist:
        return False
    norm = _resolve_ci(path, cwd)
    for allowed in allowlist:
        allowed_norm = _resolve_ci(allowed)
        if norm == allowed_norm or norm.startswith(allowed_norm.rstrip("/") + "/"):
            return True
    return False


def extract_bash_write_targets(cmd):
    """Best-effort only. Does not parse shell syntax — a target hidden
    behind a variable, a subshell, quoting tricks, or a command this list
    doesn't know about (e.g. sed -i, dd, sqlite3 writes) is invisible to
    this hook. See docs/hooks.md "Gaps"."""
    targets = []
    for pat in WRITE_BASH_PATTERNS:
        for m in re.finditer(pat, cmd):
            t = m.group(1).strip("'\"")
            if t and t not in HARMLESS_BASH_TARGETS and not t.startswith("/dev/fd/"):
                targets.append(t)
    return targets


def decide(data, config):
    """Returns (action, reason) where action is "allow" or "deny"."""
    if not is_subagent_call(data):
        return "allow", "lead session call"

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    deny_list = get_always_deny(config)
    allowlist = get_allowlist(data, config)

    cwd = data.get("cwd") or None

    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if not path:
            return "allow", "no path in tool_input"
        if path_is_denied_always(path, deny_list, cwd):
            return "deny", f"leash: denied path (always-deny list): {path}"
        if not path_is_allowed(path, allowlist, cwd):
            return "deny", f"leash: path not in worker allow-list: {path}"
        return "allow", "path in allow-list"

    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        for target in extract_bash_write_targets(cmd):
            if path_is_denied_always(target, deny_list, cwd):
                return "deny", f"leash: bash write target on always-deny list: {target}"
            if not path_is_allowed(target, allowlist, cwd):
                return "deny", (
                    "leash: bash write target not in worker allow-list "
                    f"(best-effort parse): {target}"
                )
        return "allow", "no denied bash write targets found (best-effort)"

    return "allow", f"tool not covered by leash: {tool_name}"


def _log(log_path, data, action, reason, error=None):
    if not log_path:
        return
    try:
        entry = {
            "agent_id": data.get("agent_id") if isinstance(data, dict) else None,
            "tool_name": (data.get("tool_name") if isinstance(data, dict) else None),
            "action": action,
            "reason": reason,
        }
        if error:
            entry["error"] = error
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # logging must never be the reason this hook crashes


def main(argv=None):
    config_path = os.environ.get("LEASH_CONFIG") or str(
        Path(__file__).parent / "leash.json"
    )
    log_path = os.environ.get("LEASH_LOG")

    raw = sys.stdin.read()
    data = {}
    try:
        data = json.loads(raw) if raw.strip() else {}
        config = load_leash_config(config_path)
        action, reason = decide(data, config)
    except Exception:
        # Fail-open on internal error: allow and log, never crash-block.
        # A crashing PreToolUse hook blocks every tool call, lead included.
        _log(log_path, data, "allow", "internal error, fail-open", error=traceback.format_exc())
        sys.exit(0)

    _log(log_path, data, action, reason)

    if action == "deny":
        sys.stderr.write(reason + "\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
