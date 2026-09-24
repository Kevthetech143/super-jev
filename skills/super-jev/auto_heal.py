#!/usr/bin/env python3
"""Bounded background auto-heal for stale ("preparation-required"/"refresh-required") pointers.

ask.py calls maybe_heal(pointer, principal) whenever a lookup hits a stale pointer. This never
runs the refresh inline and never blocks the caller: at most it starts a detached background
process, using the exact same prepare_path (prepare_bulk.py --refresh with the pointer's own
recorded roots/excludes/principals from refresh_changed.py's prepare_args -- same secret scan,
same held-file behavior) that a human would run by hand.

Bounds (all per principal, state kept in autoheal-state/):
  - one refresh in flight at a time per principal (lock file, stale after LOCK_STALE_SECS)
  - a cooldown per pointer (default 10 min) before it is eligible to retrigger
  - a cap on refreshes per principal per rolling hour (default 6)
Every attempt (started or skipped, and why) is appended to autoheal.log as one JSON line.

This only ever *starts* a refresh; it does not change what a lookup reports for the pointer
that triggered it (that pointer's current lookup still reports its real status). The point is
the *next* lookup, after the background refresh finishes, no longer hits the stale pointer.
"""
import json
import os
import shlex
import subprocess
import sys
import threading
import time
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
    _state_path(principal).write_text(json.dumps(state))


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


def _acquire_lock(principal: str, pointer: str) -> bool:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = _lock_path(principal)
    payload = json.dumps({"pid": os.getpid(), "ts": time.time(), "pointer": pointer}).encode()
    if _publish_lock(lock, payload):
        return True
    if _lock_holder_alive(lock):
        return False
    # Dead/stale lock: clear it and retry once. This retry still has a narrow race with
    # another racer doing the same thing, but that only matters for the rare dead-holder
    # case; the common contended case above is race-free.
    try:
        lock.unlink()
    except OSError:
        pass
    return _publish_lock(lock, payload)


def _release_lock(principal: str) -> None:
    try:
        _lock_path(principal).unlink()
    except OSError:
        pass


def is_stale_kind(kind: str) -> bool:
    return bool(kind) and kind.startswith(("preparation-required", "refresh-required"))


def maybe_heal(pointer: str, principal: str, cache_dir: Path = None,
               cooldown_secs: int = COOLDOWN_SECS, max_per_hour: int = MAX_PER_HOUR) -> str:
    """Start a bounded background refresh of `pointer` for `principal` if eligible. Returns a
    short reason string: "started", or why it was skipped ("no-report", "no-change",
    "in-progress", "cooldown", "rate-limited"). Never raises, never blocks."""
    cache_dir = cache_dir or rc.CACHE_DIR
    report_path = cache_dir / f"{pointer}-report.json"
    if not report_path.is_file():
        _log(principal=principal, pointer=pointer, action="skip", reason="no-report")
        return "no-report"
    try:
        report = json.loads(report_path.read_text())
    except ValueError:
        _log(principal=principal, pointer=pointer, action="skip", reason="no-report")
        return "no-report"
    args = rc.prepare_args(report)
    if args is None:
        _log(principal=principal, pointer=pointer, action="skip", reason="no-report")
        return "no-report"

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

    if not _acquire_lock(principal, pointer):
        _log(principal=principal, pointer=pointer, action="skip", reason="in-progress")
        return "in-progress"

    state["pointers"][pointer] = now
    state["attempts"] = recent + [now]
    _save_state(principal, state)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out_log = STATE_DIR / f"{principal}-{pointer}-last-refresh.log"
    cmd = [sys.executable, str(HERE / "prepare_bulk.py"), *args]
    lock_file = str(_lock_path(principal))
    # Detached background run: the lock is released by the child itself when it exits (success
    # or failure) so a crashed refresh cannot wedge the principal's lock past LOCK_STALE_SECS
    # anyway, but a clean exit releases it immediately rather than waiting for staleness.
    wrapper = f"{shlex.join(cmd)} >{shlex.quote(str(out_log))} 2>&1; rc=$?; rm -f {shlex.quote(lock_file)}; exit $rc"
    subprocess.Popen(["/bin/sh", "-c", wrapper], cwd=str(HERE), start_new_session=True)
    _log(principal=principal, pointer=pointer, action="started", cmd=cmd)
    return "started"
