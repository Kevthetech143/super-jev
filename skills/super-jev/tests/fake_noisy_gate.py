#!/usr/bin/env python3
"""A REAL subprocess used only by the fd-level hook-shim tests.

Unlike fake_door.py (which prints one line and always exits 0), this one is
built to reproduce the exact leak the Opus review found: a wrapped door that
writes several lines to its own stdout and stderr and exits nonzero (REJECT/
block). It is invoked for real (not monkeypatched) by
test_hook_gate_in_a_real_subprocess_never_leaks_child_output_onto_hook_stdout
in test_superjev.py, via SUPERJEV_GATE_CMD, so that test proves fd-level
behaviour: `run_door`'s `capture_output=True` in hook mode, not just a mock
of `subprocess.run`.

    python3 fake_noisy_gate.py <argv...>

Always exits 2 (REJECT), regardless of argv, after printing multiple lines
to both stdout and stderr — so a test can assert none of this text reaches
the real fd 1/2 of the `superjev.py hook gate` process that wraps it.
"""
import sys

if __name__ == "__main__":
    print("FAKE-GATE-STDOUT-TABLE-LINE-1")
    print("FAKE-GATE-STDOUT-TABLE-LINE-2")
    print("fake gate stderr", file=sys.stderr)
    sys.exit(2)
