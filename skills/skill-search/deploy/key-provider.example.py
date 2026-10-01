#!/usr/bin/env python3
"""Example key provider: prints the key and nothing else. It reads the file named by
$TYPESAFE_API_KEY_FILE, or ~/.typesafe-api-key when that is not set (the file
docs/GETTING-STARTED.md step 2 exports from). Copy this to deploy/local-key-provider.py."""
import os
import sys

path = os.environ.get("TYPESAFE_API_KEY_FILE") or os.path.expanduser("~/.typesafe-api-key")
try:
    with open(path) as f:
        sys.stdout.write(f.read().strip())
except OSError as e:
    print("cannot read key file: %s" % e, file=sys.stderr)
    sys.exit(1)
