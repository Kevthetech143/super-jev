#!/bin/sh
set -eu
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
# Memory runtime comes from the installed release checkout (two levels up).
export SUPERJEV_REPO="$(CDPATH= cd -- "$DIR/../.." && pwd -P)"
# SUPERJEV_STATE_DIR set: isolated run, config under it (same as dispatch.config_path). Else the fleet-pinned config.
if [ -n "${SUPERJEV_STATE_DIR:-}" ]; then
  exec python3 "$DIR/dispatch.py" memory --config "$SUPERJEV_STATE_DIR/_memory/config.json" "$@"
fi
exec python3 "$DIR/dispatch.py" memory --config /Users/admin/super-jev/.local/pointer-memory/config.json "$@"
