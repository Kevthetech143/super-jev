#!/usr/bin/env python3
"""Bounded background auto-heal for stale ("preparation-required"/"refresh-required") pointers.

ask.py calls maybe_heal(pointer, principal) whenever a lookup hits a stale pointer. This never
runs the refresh inline and never blocks the caller: at most it starts a detached background
process, using the exact same prepare_path (prepare_bulk.py --refresh, naming the pointer and its
principals from refresh_changed.py's prepare_args; --refresh replays the rest of the pointer's
recorded recipe, writer included -- same secret scan, same held-file behavior) that a human would
run by hand. It also calls maybe_scan on every
lookup: at most once per SCAN_SECS per principal, a detached scan finds files written into a
connected folder since its connect (which never make a pointer stale) and heals those pointers.

Bounds (state kept in autoheal-state/):
  - one refresh in flight at a time per pointer (a lock file per principal+pointer, so a long
    refresh of one set never blocks the principal's other sets; every launch, refresh or
    reconnect, takes it first, so a pointer is never run twice at once)
  - a pointer over the cap (or yielding to a waiting one) is queued; a refresh that finishes
    heals the queue one pointer at a time, oldest first, before it releases its lock (drain)
  - a cooldown per pointer (default 10 min) after a refresh that worked. An attempt that did
    not work (failed, or was still running when the lookup gave up waiting) is judged when it
    ends: it waits RETRY_SECS (doubling per consecutive failure, at most MAX_RETRY_SECS)
    instead of the full cooldown and does not count toward the hourly cap; each attempt is
    judged exactly once
  - at most MAX_CONCURRENT refreshes running at once per principal; the cap, that bound and
    the attempt record are checked and written in one locked step (_admit), so parallel
    lookups can neither overshoot them nor lose an attempt
  - a cap on started refreshes per principal per rolling hour (default 6), shared
    fairly: a pointer over the cap waits in the queue, and a pointer that already healed this
    hour queues behind any pointer that is waiting, so the busiest pointer never starves the rest
  - a refusal that repeats until the file changes (a held, secret-bearing file) is not retried
    until that file (or its recorded recipe) changes, or a day passes
Every attempt (started or skipped, and why) is appended to autoheal.log as one JSON line.

This only ever *starts* a refresh; it does not change what a lookup reports for the pointer
that triggered it (that pointer's current lookup still reports its real status). The point is
the *next* lookup, after the background refresh finishes, no longer hits the stale pointer.
"""
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import refresh_changed as rc  # noqa: E402

STATE_DIR = HERE / "autoheal-state"
LOG_PATH = STATE_DIR / "autoheal.log"

COOLDOWN_SECS = 600       # 10 minutes per pointer, after a refresh that worked
RETRY_SECS = 120          # wait after a failed/timed-out attempt (doubles per repeat, up to MAX_RETRY_SECS)
MAX_RETRY_SECS = 3600     # a set that keeps failing is tried at most once an hour
MAX_CONCURRENT = 2        # refreshes running at once per principal (each may be a paid writer run)
HELD_EXPIRE_SECS = 86400  # a "held, unchanged" mark is forgotten after a day (a scan rule may have changed)
MAX_PER_HOUR = 6          # per principal
LOCK_STALE_SECS = 4 * 3600  # a live pid older than this is a reused pid, not a refresh (none runs this long)
DETERMINISTIC_REFUSALS = ("secret-held",)  # same files, same refusal: not retried until they change


def _log(**fields) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _state_path(principal: str) -> Path:
    return STATE_DIR / f"{principal}.json"


def _load_state(principal: str) -> dict:
    p = _state_path(principal)
    if not p.is_file():
        return {"pointers": {}, "attempts": []}
    try:
        return json.loads(p.read_text())
    except ValueError:
        return {"pointers": {}, "attempts": []}


def _save_state(principal: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_DIR / f".{principal}.json.{os.getpid()}.{threading.get_ident()}"
    tmp.write_text(json.dumps(state))
    os.replace(tmp, _state_path(principal))  # a reader never sees a half-written file


@contextmanager
def _state_txn(principal: str):
    """Load, change and save the state under a file lock, so a lookup queuing a pointer and
    the lock holder's drain never overwrite each other's change."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / f".{principal}.state-lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        state = _load_state(principal)
        for key in ("pending", "retry", "fails", "held", "judged"):
            state.setdefault(key, {})
        yield state
        _save_state(principal, state)


def _mark(principal: str, pointer: str, when: float, attempts: list = None) -> None:
    """Start `pointer`'s cooldown (and record the hourly attempts) and drop it from the queue."""
    with _state_txn(principal) as state:
        state["pointers"][pointer] = when
        state["retry"].pop(pointer, None)
        state["judged"].pop(pointer, None)
        if attempts is not None:
            state["attempts"] = attempts
        state["pending"].pop(pointer, None)


def _wait_secs(state: dict, pointer: str, now: float, cooldown_secs: int = None) -> float:
    """Seconds until `pointer` may be attempted again: its retry time after a failed attempt,
    else its cooldown after the last one that worked (or is running)."""
    retry = (state.get("retry") or {}).get(pointer)
    if retry is not None:
        return retry - now
    return state["pointers"].get(pointer, 0) + (COOLDOWN_SECS if cooldown_secs is None else cooldown_secs) - now


def _live_locks(principal: str, besides: str) -> int:
    """Refreshes or reconnects running for this principal, not counting `besides` or a lock a
    drain holds while it only heals the queue (it marks it `drain`; a run of its own pointer
    drops the mark, so that run counts once)."""
    mine = _lock_path(principal, besides)
    n = 0
    for lock in STATE_DIR.glob(f"{principal}.*.lock"):
        if lock == mine:
            continue
        try:
            if json.loads(lock.read_text()).get("drain"):
                continue
        except (OSError, ValueError):
            pass
        n += _lock_holder_alive(lock)
    return n


def _admit(principal: str, pointer: str, now: float, max_per_hour: int = None,
           max_concurrent: int = None, cooldown_secs: int = None, count: bool = True) -> tuple:
    """Start an attempt: (token, "") or ("", why). The cooldown, the hourly cap, the concurrent
    bound, the pointer's lock and the attempt record are one step under the state lock, so
    parallel lookups cannot all pass a check that only the first one's record would fail.
    `count` False: an attempt that spends no writer call (a --no-findability reconnect)."""
    max_per_hour = MAX_PER_HOUR if max_per_hour is None else max_per_hour
    max_concurrent = MAX_CONCURRENT if max_concurrent is None else max_concurrent
    with _state_txn(principal) as state:
        if _wait_secs(state, pointer, now, cooldown_secs) > 0:
            return "", "cooldown"
        recent = [t for t in state["attempts"] if now - t < 3600]
        if count and len(recent) >= max_per_hour:
            return "", "rate-limited"
        if _live_locks(principal, pointer) >= max_concurrent:
            return "", "busy"
        token = _acquire_lock(principal, pointer)
        if not token:
            return "", "in-progress"
        state["pointers"][pointer] = now
        state["retry"].pop(pointer, None)
        state["judged"].pop(pointer, None)
        state["pending"].pop(pointer, None)
        state["attempts"] = recent + [now] if count else state["attempts"]
        return token, ""


def _settle(principal: str, pointer: str, ok: bool, now: float = None) -> None:
    """Judge an attempt when it ends, once (the inline wait and the child's drain may both report
    it; the first one counts). A worked attempt keeps its cooldown and clears the failure count.
    One that did not work (failed, or timed out and later failed) waits RETRY_SECS (doubling per
    consecutive failure, up to MAX_RETRY_SECS) and gives its hourly-cap slot back."""
    now = time.time() if now is None else now
    with _state_txn(principal) as state:
        if state["judged"].get(pointer):
            return
        state["judged"][pointer] = True
        if ok:
            state["fails"].pop(pointer, None)
            state["retry"].pop(pointer, None)
            return
        n = state["fails"].get(pointer, 0) + 1
        state["fails"][pointer] = n
        state["retry"][pointer] = now + min(MAX_RETRY_SECS, RETRY_SECS * 2 ** min(n - 1, 20))
        started = state["pointers"].get(pointer)
        if started in state["attempts"]:
            state["attempts"].remove(started)  # _mark stored the same time in both


def _fingerprint(paths: list, recipe: dict = None) -> str:
    """What the files are now (size and mtime) and what recipe replays them: a held file is
    retried only once this changes. A hand reconnect with the same request records the same
    recipe, so it does not change this; the HELD_EXPIRE_SECS expiry is what covers a scan-rule
    change with no edit to the files."""
    parts = [hashlib.sha256(json.dumps(recipe, sort_keys=True, default=str).encode()).hexdigest()[:16]]
    for path in sorted(paths):
        try:
            st = os.stat(os.path.expanduser(path))
            parts.append(f"{path}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            parts.append(f"{path}:missing")
    return "|".join(parts)


def _queue(principal: str, pointer: str, kind: str) -> None:
    """Queue a pointer that found the lock held; the holder's drain heals it (oldest first)."""
    with _state_txn(principal) as state:
        was = state["pending"].get(pointer) or {}
        if kind == "new" and was.get("kind") in ("refresh", "reconnect"):
            kind = was["kind"]  # a refresh or reconnect takes in the new files too; "new" may be dropped
        state["pending"][pointer] = {"kind": kind, "ts": was.get("ts", time.time())}


def _lock_path(principal: str, pointer: str) -> Path:
    return STATE_DIR / f"{principal}.{pointer}.lock"


def _lock_holder_alive(lock: Path) -> bool:
    """True if the lock is fresh and its recorded pid is still running."""
    try:
        info = json.loads(lock.read_text())
    except (OSError, ValueError):
        return False
    if time.time() - info.get("ts", 0) > LOCK_STALE_SECS:
        return False
    pid = info.get("pid")
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return not _pid_reused(pid, info.get("ts", 0))


_WALL = time.time  # the real clock and process listing, kept apart from anything that fakes them
_POPEN = subprocess.Popen


def _pid_reused(pid: int, lock_ts: float) -> bool:
    """True if the process now at `pid` started after the lock was written, so it is a different
    process that got the dead holder's pid. Every holder started before it wrote (or refreshed)
    the lock. If `ps` cannot say, assume it is the holder."""
    try:
        out = _POPEN(["ps", "-o", "etime=", "-p", str(pid)], stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True).communicate(timeout=2)[0].strip()
        days, _, rest = out.rpartition("-")
        parts = [int(x) for x in rest.split(":")]
        secs = sum(v * 60 ** i for i, v in enumerate(reversed(parts))) + (int(days) * 86400 if days else 0)
    except Exception:
        return False
    return _WALL() - secs > lock_ts + 5  # 5 s: ps reports whole seconds


def _publish_lock(lock: Path, payload: bytes) -> bool:
    """Atomically create `lock` with `payload` already fully written.

    Write the payload to a private per-thread temp file first, then publish it with
    os.link(), which -- like O_CREAT|O_EXCL -- fails with FileExistsError if the target
    already exists, so only one racing thread/process can win. Publishing this way means
    any racer that successfully sees the lock file always sees the *complete* payload:
    there is no window where the file exists but is still empty/partial. A plain
    O_CREAT|O_EXCL open followed by a separate write() left exactly that window open --
    on a busy/loaded box (observed on Linux CI, not on a quiet Mac) a second thread could
    os.open() -> FileExistsError -> read an empty file -> json.loads fails -> treated as a
    dead/stale lock -> unlink + recreate, letting several racers each "win" their own lock.
    """
    tmp = lock.parent / f".{lock.name}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        tmp.write_bytes(payload)
        try:
            os.link(str(tmp), str(lock))
            return True
        except FileExistsError:
            return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


_DRAINING = set()  # principals this process is draining (it holds one pointer's lock for it)
_OWNED = {}  # (principal, pointer) -> token of the lock this process's drain holds


@contextmanager
def _control(principal: str):
    """One short per-principal mutex around every read-check-write of the lock file (acquire,
    stale reclaim, pid/ts update, release), so a token or pid check and the write it guards
    can never interleave with another process's."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / f".{principal}.lock-control", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def _acquire_lock(principal: str, pointer: str) -> str:
    """The new lock's token (truthy) if this pointer's lock was free, else ""."""
    if (principal, pointer) in _OWNED:  # the drain's own pointer: it runs under the lock it holds
        return _OWNED[(principal, pointer)]
    lock = _lock_path(principal, pointer)
    token = uuid.uuid4().hex
    payload = json.dumps({"pid": os.getpid(), "ts": time.time(), "pointer": pointer,
                          "token": token}).encode()
    with _control(principal):
        if _publish_lock(lock, payload):
            return token
        if _lock_holder_alive(lock):
            return ""
        # Dead/stale lock: clear it and retry once (under the control mutex, so two racers
        # cannot both reclaim it).
        try:
            lock.unlink()
        except OSError:
            pass
        return token if _publish_lock(lock, payload) else ""


def _hold_lock(principal: str, pointer: str, token: str, expect_pid: int = None, **fields) -> bool:
    """Refresh our own lock (matched by token, and by pid when `expect_pid` is given): a new ts
    plus `fields`. False if it is not ours."""
    lock = _lock_path(principal, pointer)
    with _control(principal):
        try:
            info = json.loads(lock.read_text())
        except (OSError, ValueError):
            return False
        if not token or info.get("token") != token:
            return False
        if expect_pid is not None and info.get("pid") != expect_pid:
            return False
        info.update(fields, ts=time.time())
        tmp = lock.parent / f".{lock.name}.hold.{os.getpid()}.{threading.get_ident()}"
        tmp.write_text(json.dumps(info))
        os.replace(tmp, lock)
        return True


def _drain_cmd(principal: str, pointer: str, token: str) -> list:
    return [sys.executable, str(HERE / "auto_heal.py"), "--drain", principal, pointer, token]


def _child_line(cmd: list, out_log: Path, principal: str, pointer: str, token: str) -> str:
    """The shell line every lock owner's child runs: the refresh, then a detached drain that
    takes the lock over (it records its own pid), judges the attempt by the refresh's exit code
    (see _settle), heals the queue and releases the lock by its token. The line exits with the
    refresh's code right away, so a caller waiting on it (the inline reconnect) never waits on
    the drain. If the drain cannot run, the lock names a pid that is gone and the next lookup
    takes it over as dead."""
    return (f"{shlex.join(cmd)} >{shlex.quote(str(out_log))} 2>&1; rc=$?; "
            f"{shlex.join(_drain_cmd(principal, pointer, token))} \"$rc\" </dev/null >/dev/null 2>&1 & exit $rc")


def _spawn_detached(argv: list) -> "subprocess.Popen":
    """The one way every background launch starts: its own session and no inherited stdin/stdout/
    stderr, so a caller reading the ask's output to EOF never waits on a refresh."""
    return subprocess.Popen(argv, cwd=str(HERE), start_new_session=True, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _name_child(principal: str, pointer: str, token: str, proc) -> None:
    """The lock names the running child, not the lookup that started it: after the lookup exits
    a live child must still hold the lock, or the next lookup starts a second refresh. Only
    while the lock still names this lookup (a drain that already started names itself)."""
    if getattr(proc, "pid", None):
        _hold_lock(principal, pointer, token, expect_pid=os.getpid(), pid=proc.pid)


def _hand_off(principal: str, pointer: str, token: str) -> None:
    """End an inline lock owner's turn: with pointers queued (and no drain of ours already
    going on), start a detached drain that takes the lock over; else release it."""
    if principal not in _DRAINING and _load_state(principal).get("pending"):
        try:
            _spawn_detached(_drain_cmd(principal, pointer, token))
            return
        except OSError:
            pass
    _release_lock(principal, pointer, token)


def _release_lock(principal: str, pointer: str, token: str = None) -> None:
    """Remove the pointer's lock; given a token, only if the lock is still that one. A lock this
    process's drain holds is released only by the drain itself."""
    if token is not None and _OWNED.get((principal, pointer)) == token:
        return
    lock = _lock_path(principal, pointer)
    with _control(principal):
        try:
            if token is not None and json.loads(lock.read_text()).get("token") != token:
                return
            lock.unlink()
        except (OSError, ValueError):
            pass


def last_refresh_error(principal: str, pointer: str, cache_dir: Path = None) -> str:
    """The ERROR line of this pointer's last background refresh, or "" (none, still running,
    or it passed). Lets a cooling-down note say the refresh failed instead of hiding it.
    A split part (<pointer>-N) refreshes, and logs, under its parent's name."""
    pointer = _report_for(pointer, cache_dir or rc.CACHE_DIR)[1] or pointer
    try:
        text = (STATE_DIR / f"{principal}-{pointer}-last-refresh.log").read_text(errors="replace")
    except OSError:
        return ""
    return next((ln.strip()[len("ERROR:"):].strip()[:160] for ln in text.splitlines()
                 if ln.strip().startswith("ERROR:")), "")


def is_stale_kind(kind: str) -> bool:
    return bool(kind) and kind.startswith(("preparation-required", "refresh-required"))


def _report_for(pointer: str, cache_dir: Path):
    """(report, report owner) for a pointer. A split part (<pointer>-N) has no report of its
    own; its parent's report lists it under "parts", so a stale part heals via its parent."""
    names = [pointer]
    base, _, n = pointer.rpartition("-")
    if base and n.isdigit():
        names.append(base)
    for name in names:
        try:
            report = json.loads((cache_dir / f"{name}-report.json").read_text())
        except (OSError, ValueError):
            continue
        if name == pointer or any(isinstance(x, dict) and x.get("pointer") == pointer
                                  for x in report.get("parts") or []):
            return report, name
    return None, None


RECONNECT_TIMEOUT_SECS = 45  # how long a lookup waits for a reconnect; the reconnect itself has no limit


def reconnect_now(pointer: str, principal: str, cache_dir: Path = None,
                  timeout: int = RECONNECT_TIMEOUT_SECS) -> str:
    """Heal a stale pointer inline, before the ask reads it, when nothing needs a redraft.

    The service marks a pointer stale on file bytes (sha256), but maybe_heal skips any
    pointer whose files all match the prepare cache ("no-change") -- e.g. a background
    refresh that drafted the files and then did not reconnect, or a file edited and put
    back -- so such a pointer stayed stale for good. Every file here is already reviewed
    at its current bytes: the reconnect makes no writer call and runs with
    --no-findability (no Jev calls). Returns "reconnected", or why not: "no-report",
    "changed" (a file needs a redraft: use maybe_heal), "in-progress", "cooldown",
    "timeout" (still running in the background: not a failure, it is judged when it ends) or
    "failed". Never raises."""
    cache_dir = cache_dir or rc.CACHE_DIR
    report, owner = _report_for(pointer, cache_dir)
    args = rc.prepare_args(report, principal) if isinstance(report, dict) else None
    if args is None:
        return "no-report"
    cache_path = cache_dir / f"{owner}.json"
    try:
        cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    except ValueError:
        cache = {}
    if rc.changed_files(report, cache):
        return "changed"
    token, why = _admit(principal, owner, time.time(), count=False)  # no writer call: no cap slot
    if why == "cooldown":
        _log(principal=principal, pointer=owner, action="skip-reconnect", reason="cooldown")
        return "cooldown"
    if why:  # this pointer is running, or the principal is at its bound
        _queue(principal, owner, "reconnect")
        return "in-progress"
    cmd = [sys.executable, str(HERE / "prepare_bulk.py"), *args, "--no-findability"]
    out_log = STATE_DIR / f"{principal}-{owner}-last-refresh.log"
    try:
        proc = _spawn_detached(["/bin/sh", "-c", _child_line(cmd, out_log, principal, owner, token)])
    except OSError:
        _settle(principal, owner, False)
        _hand_off(principal, owner, token)
        return "failed"
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _name_child(principal, owner, token, proc)  # still running after this lookup; its drain judges it
        _log(principal=principal, pointer=owner, action="reconnect", result="timeout", cmd=cmd)
        return "timeout"
    result = "reconnected" if code in (0, 3) else "failed"  # 3 = connected, some files held
    _settle(principal, owner, result == "reconnected")
    _log(principal=principal, pointer=owner, action="reconnect", result=result, cmd=cmd)
    return result


def _memory(req: dict) -> dict:
    from connect_checked import memory
    return memory(req)


def reconnect_recipe(pointer: str, principal: str, memory=None) -> str:
    """Heal a stale pointer that prepare_bulk did not build (no report): replay the connect
    request recorded when it was connected (memory action "recipe"), at the files' current
    bytes, with the same paths, descriptions, structure and principals. Local only: no
    writer or Jev call, and the connector's own secret scan still runs. Returns
    "reconnected", or why not: "no-recipe", "manual" (a --add record re-adds itself),
    "held" (refused for a held file that has not changed since, within a day), "cooldown", "in-progress" or
    "failed". Never raises."""
    if "-manual-" in pointer:
        return "manual"
    memory = memory or _memory
    try:
        got = memory({"action": "recipe", "pointer": pointer, "principal": principal})
    except Exception:
        got = {}
    recipe = got.get("recipe") if got.get("status") == "ok" else None
    if not isinstance(recipe, dict):
        _log(principal=principal, pointer=pointer, action="skip-recipe", reason="no-recipe")
        return "no-recipe"
    state = _load_state(principal)
    paths = [str(s.get("path")) for s in recipe.get("sources") or [] if isinstance(s, dict)]
    fingerprint = _fingerprint(paths, recipe)
    mark = state.get("held", {}).get(pointer) or {}
    if mark.get("fp") == fingerprint and time.time() - mark.get("ts", 0) < HELD_EXPIRE_SECS:
        _log(principal=principal, pointer=pointer, action="skip-recipe", reason="held-unchanged")
        return "held"
    token, why = _admit(principal, pointer, time.time(), count=False)  # local only: no cap slot
    if why:
        if why == "cooldown":
            _log(principal=principal, pointer=pointer, action="skip-recipe", reason="cooldown")
        return why if why == "cooldown" else "in-progress"
    result, preview = "failed", {}
    try:
        req = {"action": "connect", "pointer": recipe["pointer"], "dataset": recipe["dataset"],
               "principals": recipe["principals"], "structure": recipe["structure"],
               "sources": recipe["sources"], "replace": True}
        preview = memory(req)
        hashes = {s.get("path"): {k: s[k] for k in ("sha256", "viewSHA", "transformSHA") if k in s}
                  for s in preview.get("sources") or []}
        if preview.get("status") != "preparation-required" or preview.get("reason") != "review-required":
            result = "failed"
        else:
            sources = [{**s, **hashes.get(os.path.abspath(os.path.expanduser(s["path"])), {})}
                       for s in recipe["sources"]]
            out = memory({**req, "sources": sources, "reviewed": True,
                           "navigationSHA": preview.get("navigationSHA")})
            if out.get("reason") == "scope-change" and out.get("registeredPrincipals"):
                # Shared since connect (share_pointers.py): the recipe names only the connect-time
                # principals; keep the pointer's registered scope rather than failing the heal.
                out = memory({**req, "principals": out["registeredPrincipals"], "sources": sources,
                              "reviewed": True, "navigationSHA": preview.get("navigationSHA")})
            result = "reconnected" if out.get("status") == "registered" else "failed"
            if result == "failed":
                preview = out
    except Exception:
        result, preview = "failed", {}
    reason = preview.get("reason") if result == "failed" else None
    _settle(principal, pointer, result == "reconnected")
    if reason in DETERMINISTIC_REFUSALS:
        with _state_txn(principal) as st:  # same files, same refusal: wait for them to change
            st["held"][pointer] = {"fp": fingerprint, "ts": time.time()}
    elif result == "reconnected":
        with _state_txn(principal) as st:
            st["held"].pop(pointer, None)
    # A pointer another lookup queued meanwhile must survive: drain that queue or release.
    _hand_off(principal, pointer, token)
    _log(principal=principal, pointer=pointer, action="reconnect-recipe", result=result, reason=reason)
    return result


def reconnect_recipe_or_queue(pointer: str, principal: str, memory=None) -> str:
    """reconnect_recipe, queuing the pointer for the lock holder's drain when the lock is held."""
    result = reconnect_recipe(pointer, principal, memory=memory)
    if result == "in-progress":
        _queue(principal, pointer, "recipe")
    return result


def maybe_heal(pointer: str, principal: str, cache_dir: Path = None,
               cooldown_secs: int = COOLDOWN_SECS, max_per_hour: int = MAX_PER_HOUR,
               new: bool = False, max_concurrent: int = None) -> str:
    """Start a bounded background refresh of `pointer` for `principal` if eligible. Returns a
    short reason string: "started", or why it was skipped ("no-report", "no-change",
    "in-progress" (this pointer is running, or MAX_CONCURRENT are: queued), "cooldown",
    "rate-limited", "failed" (the process could not start)). Never raises, never blocks. `new` (from scan):
    the pointer's folders hold files it has never seen, so it needs a refresh with no file changed."""
    cache_dir = cache_dir or rc.CACHE_DIR
    max_concurrent = MAX_CONCURRENT if max_concurrent is None else max_concurrent
    report, owner = _report_for(pointer, cache_dir)
    # A report with no recorded principal is healed as the asking agent, which the refresh then
    # records (prepare_bulk keeps the pointer's registered scope), so this happens once.
    args = rc.prepare_args(report, principal) if isinstance(report, dict) else None
    if args is None:
        _log(principal=principal, pointer=pointer, action="skip", reason="no-report")
        return "no-report"
    pointer = owner  # a split part refreshes through its parent's recorded recipe

    cache_path = cache_dir / f"{pointer}.json"
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    if not new and not rc.changed_files(report, cache):
        _log(principal=principal, pointer=pointer, action="skip", reason="no-change")
        return "no-change"

    state = _load_state(principal)
    now = time.time()
    last = state["pointers"].get(pointer, 0)
    wait = _wait_secs(state, pointer, now, cooldown_secs)
    if wait > 0:
        _log(principal=principal, pointer=pointer, action="skip", reason="cooldown",
             wait_secs=round(wait))
        return "cooldown"
    kind = "new" if new else "refresh"
    if last and now - last < 3600 and any(p != pointer for p in state.get("pending") or {}):
        # Fair share of the cap: this pointer already healed this hour and another one is waiting,
        # so it queues behind it; the drain heals them oldest first.
        _queue(principal, pointer, kind)
        token = _acquire_lock(principal, pointer)
        if token:
            _hand_off(principal, pointer, token)
        _log(principal=principal, pointer=pointer, action="skip", reason="yield", queued=True)
        return "in-progress"
    token, why = _admit(principal, pointer, now, max_per_hour, max_concurrent, cooldown_secs)
    if why in ("rate-limited", "busy", "in-progress"):
        _queue(principal, pointer, kind)  # waits its turn; never dropped
        _log(principal=principal, pointer=pointer, action="skip", reason=why, queued=True)
        return "in-progress" if why != "rate-limited" else "rate-limited"
    if why:  # cooldown, judged again under the lock
        _log(principal=principal, pointer=pointer, action="skip", reason=why)
        return why

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out_log = STATE_DIR / f"{principal}-{pointer}-last-refresh.log"
    cmd = [sys.executable, str(HERE / "prepare_bulk.py"), *args]
    # Detached background run; its drain heals the queue and releases the lock (see _child_line).
    try:
        proc = _spawn_detached(["/bin/sh", "-c", _child_line(cmd, out_log, principal, pointer, token)])
    except OSError:
        _settle(principal, pointer, False)
        _release_lock(principal, pointer, token)
        _log(principal=principal, pointer=pointer, action="skip", reason="spawn-failed")
        return "failed"
    _name_child(principal, pointer, token, proc)
    _log(principal=principal, pointer=pointer, action="started", cmd=cmd)
    return "started"


SCAN_SECS = 600           # 10 minutes per principal between new-file scans


def maybe_scan(principal: str, pointers: list, scan_secs: int = SCAN_SECS) -> str:
    """From a lookup: look for files written into this principal's connected folders since their
    connect, at most once per `scan_secs`, in a detached process (a walk can take seconds), so
    the lookup never waits. Returns "started", "recent" or "failed". Never raises."""
    now = time.time()
    try:
        with _state_txn(principal) as state:
            if now - state.get("lastScan", 0) < scan_secs:
                return "recent"
            state["lastScan"] = now
        _spawn_detached([sys.executable, str(HERE / "auto_heal.py"), "--scan", principal, *pointers])
    except (OSError, ValueError):
        return "failed"
    return "started"


def scan(principal: str, pointers: list, cache_dir: Path = None) -> dict:
    """{pointer: maybe_heal result} for each of `pointers` (prepare_bulk-built) whose folders hold
    a file no pointer accounts for. The refresh runs prepare_bulk --refresh, so each new file gets
    the same secret scan, size ceiling, description review and connect gate as a hand refresh."""
    cache_dir = cache_dir or rc.CACHE_DIR
    reports = []
    for rp in sorted(cache_dir.glob("*-report.json")):
        try:
            reports.append(json.loads(rp.read_text()))
        except (OSError, ValueError):
            continue
    known = set().union(*(rc.known_files(r) for r in reports if isinstance(r, dict)))
    out = {}
    for ptr in pointers:
        report, owner = _report_for(ptr, cache_dir)
        if not isinstance(report, dict):
            import watched
            if (cache_dir / f"{ptr}-report.json").exists() and watched.says_watched(cache_dir / f"{ptr}-report.json"):
                # a torn report that says watched: nothing can join it (an unwatched one stays silent, as before)
                line = (f"HELD  {ptr}: its report cannot be read, so nothing new joins it; "
                        + watched.recovery(ptr, cache_dir / f"{ptr}-report.json"))
                print(line)
                _log(principal=principal, pointer=ptr, action="scan-held", reason="report-unreadable", note=line)
            continue
        if owner in out:
            continue
        notes = []
        try:
            added = rc.new_files(report, known, reports, notes=notes)
        except Exception:
            continue
        for line in notes:  # a detached scan has no stdout: the log is where the person finds them
            print(line)
            _log(principal=principal, pointer=owner, action="scan-held", note=line)
        if added:
            out[owner] = maybe_heal(owner, principal, cache_dir=cache_dir, new=True)
            _log(principal=principal, pointer=owner, action="scan-new", new=len(added),
                 first=Path(added[0]).name, result=out[owner])
    return out


def _log_refusal(principal: str, pointer: str) -> None:
    """A refresh that was refused (exit 2) says why in its last-refresh log; the shared log carries it too."""
    try:
        for ln in (STATE_DIR / f"{principal}-{pointer}-last-refresh.log").read_text(errors="replace").splitlines():
            if ln.startswith(("REFUSED", "WATCH REMOVED", "  HELD")):
                _log(principal=principal, pointer=pointer, action="refresh-refused", note=ln[:400])
    except OSError:
        pass


def _drain_prepare(pointer: str, principal: str, kind: str) -> str:
    """Heal one queued prepare_bulk pointer inline, under the drain's lock, with maybe_heal's and
    reconnect_now's own rules: changed files get a refresh (per-pointer cooldown, hourly cap);
    a pointer queued by reconnect_now with nothing to redraft gets the --no-findability
    reconnect; one queued by scan ("new") gets the refresh that admits its new files. Returns
    "refreshed", "reconnected", or why not."""
    report, owner = _report_for(pointer, rc.CACHE_DIR)
    if not isinstance(report, dict):
        return "no-report"
    # A pointer scan queued for new files is refreshed like a changed one, unless they were taken
    # in since it was queued: a refresh reconnects with replace:true, rotating approved answers.
    new = kind == "new" and bool(rc.new_files(report, rc.known_across(rc.CACHE_DIR)))
    args = rc.prepare_args(report, principal)  # no recorded principal: the asker, recorded by the refresh
    if args is None:
        return "no-report"
    cache_path = rc.CACHE_DIR / f"{owner}.json"
    try:
        cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    except ValueError:
        cache = {}
    changed = bool(rc.changed_files(report, cache)) or new
    if not changed and kind != "reconnect":
        return "no-change"  # refreshed since it was queued
    cmd = [sys.executable, str(HERE / "prepare_bulk.py"), *args]
    if not changed:
        cmd.append("--no-findability")
    # The same admission as every launch: cooldown, cap, bound, and the pointer's one lock (the
    # drain's own pointer runs under the lock the drain already holds).
    token, why = _admit(principal, owner, time.time(), count=changed)
    if why:
        return why if why != "busy" else "in-progress"
    try:
        try:
            with (STATE_DIR / f"{principal}-{owner}-last-refresh.log").open("w") as out:
                code = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, cwd=str(HERE)).returncode
        except OSError:
            code = 1
    finally:
        _release_lock(principal, owner, token)
    _settle(principal, owner, code in (0, 3))  # 3 = connected, with some files held: the pointer itself was refreshed
    if code == 2:
        _log_refusal(principal, owner)
    if code not in (0, 3):
        return "failed"
    return "refreshed" if changed else "reconnected"


def drain(principal: str, pointer: str, token: str, memory=None, rc: int = None) -> int:
    """Run by the refresh holding the lock, before it releases it: heal the queued pointers one
    at a time, oldest first, each at most once per drain, then release the lock. A pointer still
    cooling down, over the hourly cap or running elsewhere stays queued for the next drain.
    `rc` is the exit code of the refresh this drain follows: it judges that attempt (_settle).
    Touches nothing if the lock is no longer this refresh's (token)."""
    tried = set()
    if not _hold_lock(principal, pointer, token, pid=os.getpid(), drain=True):
        return 0  # the lock is no longer this refresh's: touch nothing
    if rc is not None:
        _settle(principal, pointer, rc in (0, 3))
        if rc == 2:
            _log_refusal(principal, pointer)
    _DRAINING.add(principal)
    _OWNED[(principal, pointer)] = token
    try:
        while True:
            pending = _load_state(principal).get("pending") or {}
            todo = sorted((v.get("ts", 0), p) for p, v in pending.items() if p not in tried)
            # Refresh the lock's ts per pointer so a long drain is not taken for a dead one.
            if not todo or not _hold_lock(principal, pointer, token):
                return 0
            queued = todo[0][1]
            tried.add(queued)
            kind = pending[queued].get("kind")
            if queued == pointer:  # its own pointer runs under this lock: a run, so it counts
                _hold_lock(principal, pointer, token, drain=False)
            try:
                if kind == "recipe":
                    result = reconnect_recipe(queued, principal, memory=memory)
                else:
                    result = _drain_prepare(queued, principal, kind)
            except Exception:
                result = "failed"
            if queued == pointer:
                _hold_lock(principal, pointer, token, drain=True)
            if result not in ("cooldown", "rate-limited", "in-progress"):
                with _state_txn(principal) as state:
                    state["pending"].pop(queued, None)
            _log(principal=principal, pointer=queued, action="drain", kind=kind, result=result)
    finally:
        _OWNED.pop((principal, pointer), None)
        _release_lock(principal, pointer, token)
        _DRAINING.discard(principal)


if __name__ == "__main__" and sys.argv[1:2] == ["--drain"] and len(sys.argv) in (5, 6):
    sys.exit(drain(sys.argv[2], sys.argv[3], sys.argv[4],
                   rc=int(sys.argv[5]) if len(sys.argv) == 6 and sys.argv[5].lstrip("-").isdigit() else None))
if __name__ == "__main__" and sys.argv[1:2] == ["--scan"] and len(sys.argv) >= 3:
    scan(sys.argv[2], sys.argv[3:])
    sys.exit(0)
