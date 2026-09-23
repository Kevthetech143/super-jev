#!/usr/bin/env python3
"""Example key provider: prints the contents of the file named by
$TYPESAFE_API_KEY_FILE. Point TYPESAFE_API_KEY_FILE at your own secret file."""
import os
import sys

path = os.environ.get("TYPESAFE_API_KEY_FILE", "")
if not path:
    print("TYPESAFE_API_KEY_FILE is not set", file=sys.stderr)
    sys.exit(1)
try:
    with open(path) as f:
        sys.stdout.write(f.read().strip())
except OSError as e:
    print("cannot read key file: %s" % e, file=sys.stderr)
    sys.exit(1)
