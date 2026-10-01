#!/usr/bin/env bash
# One-line installer for Super Jev's terminal chat CLI ("superjev").
#
#   bash install.sh
#
# Safe and idempotent: re-running just updates the checkout and re-links the
# binary. Never overwrites your API key. Requires Node >= 24 and git; the
# helpers need python3, which the app checks when it starts, not here.
set -euo pipefail

REPO_URL="${SUPERJEV_REPO_URL:-https://github.com/Kevthetech143/super-jev.git}"
INSTALL_DIR="${SUPERJEV_INSTALL_DIR:-$HOME/.local/share/super-jev}"
BIN_DIR="${SUPERJEV_BIN_DIR:-$HOME/.local/bin}"

info()  { printf '\033[36m==>\033[0m %s\n' "$1"; }
fail()  { printf '\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }

# The exact Node steps, the same lines setup.py prints and the docs repeat. Into your home
# folder, no admin rights needed.
node_steps() {
  {
    printf '\033[31merror:\033[0m %s\n' "$1"
    echo "Install Node 24 or newer, then run this installer again:"
    case "${SHELL:-}" in
      *zsh*) cat <<'ZSH'
  touch ~/.zshrc
ZSH
        ;;
    esac
    cat <<'NVM'
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash
  \. "$HOME/.nvm/nvm.sh"
  nvm install 24
NVM
    echo "Already use Homebrew? Run: brew install node"
  } >&2
  exit 1
}

command -v node >/dev/null 2>&1 || node_steps "Node.js is required (>=24) and was not found."
node_major=$(node -e 'console.log(process.versions.node.split(".")[0])')
if [ "$node_major" -lt 24 ]; then
  node_steps "Node >=24 is required, found $(node -v)."
fi
command -v git >/dev/null 2>&1 || fail "git is required."

mkdir -p "$BIN_DIR"

if [ -d "$INSTALL_DIR/.git" ]; then
  info "Updating existing install in $INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch --quiet origin
  git -C "$INSTALL_DIR" merge --quiet --ff-only '@{u}' \
    || fail "Could not fast-forward $INSTALL_DIR (local changes?). Fix or remove it and re-run."

else
  info "Cloning super-jev into $INSTALL_DIR"
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone --quiet "$REPO_URL" "$INSTALL_DIR"
fi

info "Linking superjev to $BIN_DIR"
if [ -e "$BIN_DIR/superjev" ] && ! grep -qE 'super-jev-installer|jev-chat-cli\.ts' "$BIN_DIR/superjev" 2>/dev/null; then
  fail "$BIN_DIR/superjev exists and was not made by this installer; not overwriting it."
fi
cat > "$BIN_DIR/superjev" <<EOF
#!/usr/bin/env bash
# super-jev-installer
exec node "$INSTALL_DIR/src/jev-chat-cli.ts" "\$@"
EOF
chmod +x "$BIN_DIR/superjev"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) printf '\n\033[33mNote:\033[0m %s is not on your PATH.\nAdd this to your shell profile:\n  export PATH="%s:$PATH"\n' "$BIN_DIR" "$BIN_DIR" ;;
esac

info "Installed. Run: superjev"
