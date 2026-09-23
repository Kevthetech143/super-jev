#!/usr/bin/env python3
"""Fake gate/verify door used only so door_missing() finds a real file.

test_superjev.py mostly monkeypatches subprocess.run before a door is
actually invoked, so this script's body normally never executes — its
main job is to *exist on disk* at a path JEV_LIB / FLEET_VERIFY_PY
can be pointed at in tests, standing in for the fleet-local doors that
are real on the machine this skill was authored on but absent on a
fresh checkout (like a CI runner). Run with no env vars set, it just
echoes its argv and exits 0, so a stray invocation fails loudly rather
than hanging or reaching anything live.

A handful of tests DO run this file for real (via SUPERJEV_GATE_CMD /
SUPERJEV_VERIFY_CMD), to prove the hook shim's strong-flag mapping works
across a real subprocess boundary, not just against a mocked
subprocess.run. Two env vars, read only by this file, drive that:

    FAKE_DOOR_STDOUT_FILE   path to a file whose exact contents this
                            script prints to stdout verbatim (a canned
                            jev.py/worker-verify table fixture). Passing
                            canned text as a file rather than inline in
                            an env var sidesteps all shell-quoting of a
                            multi-line fixture.
    FAKE_DOOR_EXIT          the exit code to return (default 0).

    python3 fake_door.py <argv...>
"""
import os
import sys

if __name__ == "__main__":
    stdout_file = os.environ.get("FAKE_DOOR_STDOUT_FILE")
    if stdout_file:
        sys.stdout.write(open(stdout_file, encoding="utf-8").read())
    else:
        print("fake_door:", sys.argv[1:])
    sys.exit(int(os.environ.get("FAKE_DOOR_EXIT", "0")))
