#!/usr/bin/env python3
"""An ask never waits on a refresh: the background refresh must not hold the ask's stdout open.
A caller that reads the ask's output to EOF has to get EOF right away, while the refresh keeps
running and writes its own log. Runs the real launcher in a child process with a fake slow
refresh (no paid calls).

    python3 -m pytest skills/super-jev/tests/test_auto_heal_never_wait.py -q
"""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
REFRESH_SECS = 5

CHILD = r'''
import hashlib, importlib.util, json, sys
from pathlib import Path
tmp, skill = Path(sys.argv[1]), Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("auto_heal_nw", skill / "auto_heal.py")
ah = importlib.util.module_from_spec(spec); spec.loader.exec_module(ah)
fake = tmp / "fake"; fake.mkdir()
(fake / "prepare_bulk.py").write_text(
    "import time, pathlib\nprint('refresh-output', flush=True)\ntime.sleep(%d)\n"
    "pathlib.Path(%r).write_text('done')\n" % (int(sys.argv[3]), str(tmp / "done")))
ah.HERE = fake
ah.STATE_DIR = tmp / "state"; ah.LOG_PATH = ah.STATE_DIR / "autoheal.log"
cache = tmp / "cache"; cache.mkdir(); ah.rc.CACHE_DIR = cache
root = tmp / "brain"; root.mkdir(); f = root / "moving.md"; f.write_text("# moving\n")
(cache / "moving.json").write_text("{}")
(cache / "moving-report.json").write_text(json.dumps({
    "pointer": "moving", "roots": [str(root)], "approved": [str(f)], "excludes": [],
    "noRecurse": True, "principal": "agent", "principals": ["agent"]}))
print(ah.maybe_heal("moving", "agent"), flush=True)
'''


def test_ask_output_reaches_eof_while_refresh_runs(tmp_path):
    t0 = time.time()
    p = subprocess.Popen([sys.executable, "-c", CHILD, str(tmp_path), str(SKILL), str(REFRESH_SECS)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = p.stdout.read()  # what an agent's shell tool does: read until EOF
    waited = time.time() - t0
    p.wait()
    assert out.strip().endswith("started"), out
    assert waited < 2, f"the ask's output stayed open {waited:.1f}s (refresh takes {REFRESH_SECS}s)"
    assert not (tmp_path / "done").exists()  # the refresh is still running in the background
    deadline = time.time() + REFRESH_SECS + 15
    while time.time() < deadline and not (tmp_path / "done").exists():
        time.sleep(0.2)
    assert (tmp_path / "done").exists()  # and it completes on its own
    log = tmp_path / "state" / "agent-moving-last-refresh.log"
    assert "refresh-output" in log.read_text()  # writing its own log, unchanged
