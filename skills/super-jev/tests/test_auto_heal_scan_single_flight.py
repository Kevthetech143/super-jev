#!/usr/bin/env python3
"""auto_heal --scan is single-flight per principal: a second concurrent scan for the same
principal exits 0 at once without scanning; a different principal still runs. State is a temp dir.

    python3 -m pytest skills/super-jev/tests/test_auto_heal_scan_single_flight.py -q
"""
import subprocess
import sys
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent

CHILD = r'''
import importlib.util, sys, time
from pathlib import Path
tmp, skill, principal, secs = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], float(sys.argv[4])
spec = importlib.util.spec_from_file_location("auto_heal_sf", skill / "auto_heal.py")
ah = importlib.util.module_from_spec(spec); spec.loader.exec_module(ah)
ah.STATE_DIR = tmp / "state"
def fake_scan(p, ptrs):
    (tmp / f"scanned-{p}-{time.time_ns()}").write_text("x")
    (tmp / f"started-{p}").write_text("x")
    time.sleep(secs)
ah.scan = fake_scan
sys.exit(ah.scan_single_flight(principal, []))
'''


def _run(tmp, principal, secs):
    return subprocess.Popen([sys.executable, "-c", CHILD, str(tmp), str(SKILL), principal, str(secs)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _wait_started(tmp, principal):
    deadline = time.time() + 15
    while time.time() < deadline and not (tmp / f"started-{principal}").exists():
        time.sleep(0.05)
    assert (tmp / f"started-{principal}").exists()


def test_second_scan_same_principal_exits_fast_without_scanning(tmp_path):
    first = _run(tmp_path, "pa", 4)
    _wait_started(tmp_path, "pa")
    t0 = time.time()
    second = _run(tmp_path, "pa", 4)
    out, _ = second.communicate(timeout=3)
    assert second.returncode == 0 and "already running" in out, out
    assert time.time() - t0 < 3
    assert len(list(tmp_path.glob("scanned-pa-*"))) == 1  # only the first scanned
    first.communicate(timeout=15)
    assert first.returncode == 0


def test_different_principals_both_run_and_lock_frees_after_exit(tmp_path):
    a = _run(tmp_path, "pa", 2)
    _wait_started(tmp_path, "pa")
    b = _run(tmp_path, "pb", 0)
    b.communicate(timeout=10)
    assert b.returncode == 0 and list(tmp_path.glob("scanned-pb-*"))
    a.communicate(timeout=15)
    c = _run(tmp_path, "pa", 0)  # the first has exited: its lock is gone
    c.communicate(timeout=10)
    assert len(list(tmp_path.glob("scanned-pa-*"))) == 2
