#!/usr/bin/env python3
"""Example key provider: prints the key and nothing else. It reads the file judges.key_file_path()
names: $TYPESAFE_API_KEY_FILE, or ~/.typesafe-api-key when that is not set (the file
docs/GETTING-STARTED.md step 2 exports from). Copy this to deploy/local-key-provider.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "super-jev"))
import judges  # noqa: E402

try:
    with open(judges.key_file_path()) as f:
        sys.stdout.write(f.read().strip())
except OSError as e:
    print("cannot read key file: %s" % e, file=sys.stderr)
    sys.exit(1)
