#!/usr/bin/env python3
"""Bounded background auto-heal for stale ("preparation-required"/"refresh-required") pointers.

ask.py calls maybe_heal(pointer, principal) whenever a lookup hits a stale pointer. This never
runs the refresh inline and never blocks the caller: at most it starts a detached background
process, using the exact same prepare_path (prepare_bulk.py --refresh with the pointer's own
recorded roots/excludes/principals from refresh_changed.py's prepare_args -- same secret scan,
same held-file behavior) that a human would run by hand.

Bounds (all per principal, state kept in autoheal-state/):
  - one refresh in flight at a time per principal (lock file, stale after LOCK_STALE_SECS)
  - a pointer that finds the lock held is queued; the refresh holding the lock heals the queue
    one pointer at a time, oldest first, before it releases the lock (drain), so a pointer
    that sorts after others is never skipped for good while they keep changing
  - a cooldown per pointer (default 10 min) before it is eligible to retrigger
  - a cap on refreshes per principal per rolling hour (default 6)
Every attempt (started or skipped, and why) is appended to autoheal.log as one JSON line.

This only ever *starts* a refresh; it does not change what a lookup reports for the pointer
that triggered it (that pointer's current lookup still reports its real status). The point is
the *next* lookup, after the background refresh finishes, no longer hits the stale pointer.
"""
import fcntl
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

COOLDOWN_SECS = 600       # 10 minutes per pointer
MAX_PER_HOUR = 6          # per principal
LOCK_STALE_SECS = 1800    # 30 minutes: a lock older than this is treated as dead


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
        state.setdefault("pending", {})
        yield state
        _save_state(principal, state)


def _mark(principal: str, pointer: str, when: float, attempts: list = None) -> None:
    """Start `pointer`'s cooldown (and record the hourly attempts) and drop it from the queue."""
    with _state_txn(principal) as state:
        state["pointers"][pointer] = when
        if attempts is not None:
            state["attempts"] = attempts
        state["pending"].pop(pointer, None)


def _queue(principal: str, pointer: str, kind: str) -> None:
    """Queue a pointer that found the lock held; the holder's drain heals it (oldest first)."""
    with _state_txn(principal) as state:
        first = (state["pending"].get(pointer) or {}).get("ts", time.time())
        state["pending"][pointer] = {"kind": kind, "ts": first}


def _lock_path(principal: str) -> Path:
    return STATE_DIR / f"{principal}.lock"


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
    return True


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


_DRAINING = {}  # principal -> token of the lock this process's drain holds


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
    """The new lock's token (truthy) if acquired, else "". Inside a drain, the drain's own
    lock (it is already held for this principal by this process)."""
    if principal in _DRAINING:
        return _DRAINING[principal]
    lock = _lock_path(principal)
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


def _hold_lock(principal: str, token: str, expect_pid: int = None, **fields) -> bool:
    """Refresh our own lock (matched by token, and by pid when `expect_pid` is given): a new ts
    plus `fields`. False if it is not ours."""
    lock = _lock_path(principal)
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


def _drain_cmd(principal: str, token: str) -> list:
    return [sys.executable, str(HERE / "auto_heal.py"), "--drain", principal, token]


def _child_line(cmd: list, out_log: Path, principal: str, token: str) -> str:
    """The shell line every lock owner's child runs: the refresh, then a detached drain that
    takes the lock over (it records its own pid), heals the queue and releases the lock by its
    token. The line exits with the refresh's code right away, so a caller waiting on it (the
    inline reconnect) never waits on the drain. If the drain cannot run, the lock names a pid
    that is gone and the next lookup takes it over as dead."""
    return (f"{shlex.join(cmd)} >{shlex.quote(str(out_log))} 2>&1; rc=$?; "
            f"{shlex.join(_drain_cmd(principal, token))} </dev/null >/dev/null 2>&1 & exit $rc")


def _name_child(principal: str, token: str, proc) -> None:
    """The lock names the running child, not the lookup that started it: after the lookup exits
    a live child must still hold the lock, or the next lookup starts a second refresh. Only
    while the lock still names this lookup (a drain that already started names itself)."""
    if getattr(proc, "pid", None):
        _hold_lock(principal, token, expect_pid=os.getpid(), pid=proc.pid)


def _hand_off(principal: str, token: str) -> None:
    """End an inline lock owner's turn: inside a drain, nothing (the drain goes on); with
    pointers queued, start a detached drain that takes the lock over; else release it."""
    if principal in _DRAINING:
        return
    if _load_state(principal).get("pending"):
        try:
            subprocess.Popen(_drain_cmd(principal, token), cwd=str(HERE), start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    _release_lock(principal, token)


def _release_lock(principal: str, token: str = None) -> None:
    """Remove the lock; given a token, only if the lock is still that one. Inside a drain only
    the drain itself (with its token) releases it."""
    if principal in _DRAINING and token != _DRAINING[principal]:
        return
    lock = _lock_path(principal)
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


RECONNECT_TIMEOUT_SECS = 45


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
    "timeout" (left running in the background) or "failed". Never raises."""
    cache_dir = cache_dir or rc.CACHE_DIR
    report, owner = _report_for(pointer, cache_dir)
    args = rc.prepare_args(report) if isinstance(report, dict) else None
    if args is None:
        return "no-report"
    cache_path = cache_dir / f"{owner}.json"
    try:
        cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    except ValueError:
        cache = {}
    if rc.changed_files(report, cache):
        return "changed"
    state = _load_state(principal)
    if time.time() - state["pointers"].get(owner, 0) < COOLDOWN_SECS:
        _log(principal=principal, pointer=owner, action="skip-reconnect", reason="cooldown")
        return "cooldown"
    token = _acquire_lock(principal, owner)
    if not token:
        _queue(principal, owner, "reconnect")
        return "in-progress"
    cmd = [sys.executable, str(HERE / "prepare_bulk.py"), *args, "--no-findability"]
    out_log = STATE_DIR / f"{principal}-{owner}-last-refresh.log"
    try:
        proc = subprocess.Popen(["/bin/sh", "-c", _child_line(cmd, out_log, principal, token)],
                                cwd=str(HERE), start_new_session=True)
    except OSError:
        _hand_off(principal, token)
        return "failed"
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _name_child(principal, token, proc)  # left running after this lookup
        _log(principal=principal, pointer=owner, action="reconnect", result="timeout", cmd=cmd)
        return "timeout"
    result = "reconnected" if code == 0 else "failed"
    if code == 0:
        # Cooldown only after a success: a failed or timed-out reconnect leaves the
        # pointer eligible for maybe_heal's background refresh.
        _mark(principal, owner, time.time())
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
    "cooldown", "in-progress" or "failed". Never raises."""
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
    if time.time() - state["pointers"].get(pointer, 0) < COOLDOWN_SECS:
        _log(principal=principal, pointer=pointer, action="skip-recipe", reason="cooldown")
        return "cooldown"
    token = _acquire_lock(principal, pointer)
    if not token:
        return "in-progress"
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
    finally:
        # Under the state lock, not the state read before this lock was taken: a pointer
        # another lookup queued meanwhile must survive. Then drain that queue or release.
        if result == "reconnected":
            _mark(principal, pointer, time.time())
        _hand_off(principal, token)
    _log(principal=principal, pointer=pointer, action="reconnect-recipe", result=result,
         reason=preview.get("reason") if result == "failed" else None)
    return result


def reconnect_recipe_or_queue(pointer: str, principal: str, memory=None) -> str:
    """reconnect_recipe, queuing the pointer for the lock holder's drain when the lock is held."""
    result = reconnect_recipe(pointer, principal, memory=memory)
    if result == "in-progress":
        _queue(principal, pointer, "recipe")
    return result


def maybe_heal(pointer: str, principal: str, cache_dir: Path = None,
               cooldown_secs: int = COOLDOWN_SECS, max_per_hour: int = MAX_PER_HOUR) -> str:
    """Start a bounded background refresh of `pointer` for `principal` if eligible. Returns a
    short reason string: "started", or why it was skipped ("no-report", "no-change",
    "in-progress", "cooldown", "rate-limited"). Never raises, never blocks."""
    cache_dir = cache_dir or rc.CACHE_DIR
    report, owner = _report_for(pointer, cache_dir)
    args = rc.prepare_args(report) if isinstance(report, dict) else None
    if args is None:
        _log(principal=principal, pointer=pointer, action="skip", reason="no-report")
        return "no-report"
    pointer = owner  # a split part refreshes through its parent's recorded recipe

    cache_path = cache_dir / f"{pointer}.json"
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    if not rc.changed_files(report, cache):
        _log(principal=principal, pointer=pointer, action="skip", reason="no-change")
        return "no-change"

    state = _load_state(principal)
    now = time.time()
    last = state["pointers"].get(pointer, 0)
    if now - last < cooldown_secs:
        _log(principal=principal, pointer=pointer, action="skip", reason="cooldown",
             wait_secs=round(cooldown_secs - (now - last)))
        return "cooldown"
    recent = [t for t in state["attempts"] if now - t < 3600]
    if len(recent) >= max_per_hour:
        _log(principal=principal, pointer=pointer, action="skip", reason="rate-limited",
             attempts_last_hour=len(recent))
        return "rate-limited"

    token = _acquire_lock(principal, pointer)
    if not token:
        _queue(principal, pointer, "refresh")
        _log(principal=principal, pointer=pointer, action="skip", reason="in-progress", queued=True)
        return "in-progress"

    _mark(principal, pointer, now, recent + [now])

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out_log = STATE_DIR / f"{principal}-{pointer}-last-refresh.log"
    cmd = [sys.executable, str(HERE / "prepare_bulk.py"), *args]
    # Detached background run; its drain heals the queue and releases the lock (see _child_line).
    proc = subprocess.Popen(["/bin/sh", "-c", _child_line(cmd, out_log, principal, token)],
                            cwd=str(HERE), start_new_session=True)
    _name_child(principal, token, proc)
    _log(principal=principal, pointer=pointer, action="started", cmd=cmd)
    return "started"


def _drain_prepare(pointer: str, principal: str, kind: str) -> str:
    """Heal one queued prepare_bulk pointer inline, under the drain's lock, with maybe_heal's and
    reconnect_now's own rules: changed files get a refresh (per-pointer cooldown, hourly cap);
    a pointer queued by reconnect_now with nothing to redraft gets the --no-findability
    reconnect. Returns "refreshed", "reconnected", or why not."""
    report, owner = _report_for(pointer, rc.CACHE_DIR)
    args = rc.prepare_args(report) if isinstance(report, dict) else None
    if args is None:
        return "no-report"
    cache_path = rc.CACHE_DIR / f"{owner}.json"
    try:
        cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    except ValueError:
        cache = {}
    changed = bool(rc.changed_files(report, cache))
    if not changed and kind != "reconnect":
        return "no-change"  # refreshed since it was queued
    state, now = _load_state(principal), time.time()
    if now - state["pointers"].get(owner, 0) < COOLDOWN_SECS:
        return "cooldown"
    cmd = [sys.executable, str(HERE / "prepare_bulk.py"), *args]
    if changed:
        recent = [t for t in state["attempts"] if now - t < 3600]
        if len(recent) >= MAX_PER_HOUR:
            return "rate-limited"
        _mark(principal, owner, now, recent + [now])
    else:
        cmd.append("--no-findability")
    try:
        with (STATE_DIR / f"{principal}-{owner}-last-refresh.log").open("w") as out:
            code = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, cwd=str(HERE),
                                  timeout=LOCK_STALE_SECS // 2).returncode
    except (OSError, subprocess.TimeoutExpired):
        code = 1
    if code:
        return "failed"
    if not changed:
        _mark(principal, owner, time.time())
    return "refreshed" if changed else "reconnected"


def drain(principal: str, token: str, memory=None) -> int:
    """Run by the refresh holding the lock, before it releases it: heal the queued pointers one
    at a time, oldest first, each at most once per drain, then release the lock. A pointer still
    cooling down or over the hourly cap stays queued for the next drain. Touches nothing if the
    lock is no longer this refresh's (token)."""
    tried = set()
    if not _hold_lock(principal, token, pid=os.getpid()):
        return 0  # the lock is no longer this refresh's: touch nothing
    _DRAINING[principal] = token
    try:
        while True:
            pending = _load_state(principal).get("pending") or {}
            todo = sorted((v.get("ts", 0), p) for p, v in pending.items() if p not in tried)
            # Refresh the lock's ts per pointer so a long drain is not taken for a dead one.
            if not todo or not _hold_lock(principal, token):
                return 0
            pointer = todo[0][1]
            tried.add(pointer)
            kind = pending[pointer].get("kind")
            try:
                if kind == "recipe":
                    result = reconnect_recipe(pointer, principal, memory=memory)
                else:
                    result = _drain_prepare(pointer, principal, kind)
            except Exception:
                result = "failed"
            if result not in ("cooldown", "rate-limited"):
                with _state_txn(principal) as state:
                    state["pending"].pop(pointer, None)
            _log(principal=principal, pointer=pointer, action="drain", kind=kind, result=result)
    finally:
        _release_lock(principal, token)
        _DRAINING.pop(principal, None)


if __name__ == "__main__" and sys.argv[1:2] == ["--drain"] and len(sys.argv) == 4:
    sys.exit(drain(sys.argv[2], sys.argv[3]))
