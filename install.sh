#!/usr/bin/env bash
# One-line installer for Super Jev's terminal chat CLI ("superjev").
#
#   curl -fsSL https://raw.githubusercontent.com/<org>/super-jev/main/install.sh | bash
#
# Safe and idempotent: re-running just re-installs deps and re-links the
# binary. Never overwrites your config or API key. Requires Node >= 24 and
# git; python3 is used by the cache lookup (skills/super-jev/ask.py) and is
# optional at install time (checked at first chat run instead).
set -euo pipefail

REPO_URL="${SUPERJEV_REPO_URL:-https://github.com/Kevthetech143/super-jev.git}"
INSTALL_DIR="${SUPERJEV_INSTALL_DIR:-$HOME/.local/share/super-jev}"
BIN_DIR="${SUPERJEV_BIN_DIR:-$HOME/.local/bin}"

info()  { printf '\033[36m==>\033[0m %s\n' "$1"; }
fail()  { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }

command -v node >/dev/null 2>&1 || fail "Node.js is required (>=24). Install it first: https://nodejs.org"
node_major=$(node -e 'console.log(process.versions.node.split(".")[0])')
if [ "$node_major" -lt 24 ]; then
  fail "Node >=24 required, found $(node -v). Upgrade Node and re-run."
fi
command -v git >/dev/null 2>&1 || fail "git is required."

mkdir -p "$BIN_DIR"

if [ -d "$INSTALL_DIR/.git" ]; then
  info "Updating existing install in $INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch --quiet origin
  git -C "$INSTALL_DIR" reset --quiet --hard origin/HEAD 2>/dev/null || true
else
  info "Cloning super-jev into $INSTALL_DIR"
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone --quiet "$REPO_URL" "$INSTALL_DIR"
fi

info "Installing dependencies"
(cd "$INSTALL_DIR" && npm install --omit=dev --no-fund --no-audit --silent)

info "Linking superjev to $BIN_DIR"
cat > "$BIN_DIR/superjev" <<EOF
#!/usr/bin/env bash
exec node "$INSTALL_DIR/src/jev-chat-cli.ts" "\$@"
EOF
chmod +x "$BIN_DIR/superjev"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) printf '\n\033[33mNote:\033[0m %s is not on your PATH.\nAdd this to your shell profile:\n  export PATH="%s:$PATH"\n' "$BIN_DIR" "$BIN_DIR" ;;
esac

info "Installed. Run: superjev"
