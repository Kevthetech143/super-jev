#!/usr/bin/env python3
"""Fake gate/verify door used only so door_missing() finds a real file.

test_superjev.py always monkeypatches subprocess.run before a door is
actually invoked, so this script's body normally never executes — its
only job is to *exist on disk* at a path FLEET_JEV_LIB / FLEET_VERIFY_PY
can be pointed at in tests, standing in for the fleet-local doors that
are real on the machine this skill was authored on but absent on a
fresh checkout (like a CI runner). If it is ever run for real, it just
echoes its argv and exits 0, so a stray invocation fails loudly rather
than hanging or reaching anything live.
"""
import sys

if __name__ == "__main__":
    print("fake_door:", sys.argv[1:])
    sys.exit(0)
