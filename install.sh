#!/usr/bin/env bash
# imessage-search installer — idempotent. Builds an isolated venv, installs the typedstream
# parser, drops a launcher on your PATH, then verifies it can read your Messages database.
set -euo pipefail

APP="imessage-search"
SRC="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/$APP"
BIN="$HOME/.local/bin"
VENV="$DATA/venv"

echo "==> Installing $APP"

# 1. Prerequisites
if [[ "$(uname)" != "Darwin" ]]; then
  echo "ERROR: macOS only (reads ~/Library/Messages/chat.db)." >&2; exit 1
fi
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found. Install Python 3, then re-run." >&2; exit 1; }

# 2. Layout + isolated venv (PEP 668-safe; no system packages touched)
mkdir -p "$DATA" "$BIN"
cp "$SRC/imessage_search.py" "$DATA/imessage_search.py"
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
if ! "$VENV/bin/pip" install --quiet pytypedstream >/dev/null; then
  echo "ERROR: could not install pytypedstream (network / proxy / PyPI unreachable?)." >&2
  echo "       Restore connectivity and re-run: bash install.sh" >&2
  exit 1
fi
echo "    venv + pytypedstream ready at $VENV"

# 3. Launcher on PATH
cat > "$BIN/$APP" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python" "$DATA/imessage_search.py" "\$@"
EOF
chmod +x "$BIN/$APP"
echo "    launcher installed at $BIN/$APP"

case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "    NOTE: $BIN is not on your PATH. Add it: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc && source ~/.zshrc" ;;
esac

# 4. Verify Full Disk Access (the one step no installer can do for you)
echo "==> Verifying access to ~/Library/Messages/chat.db ..."
if "$BIN/$APP" recent 1 >/dev/null 2>&1; then
  echo "    OK — installed and your Messages database is readable."
  echo ""
  echo "Try it:  $APP recent 5"
else
  echo ""
  echo "    Installed, but it could NOT read your Messages database yet."
  echo "    Grant Full Disk Access to the app that runs this command:"
  echo ""
  echo "      System Settings  >  Privacy & Security  >  Full Disk Access"
  echo "      > click +, add your terminal (Terminal / iTerm) or your Claude host app"
  echo "      > then FULLY QUIT and reopen that app (the grant only takes effect on restart)"
  echo ""
  echo "    Then verify:  $APP recent 5"
  exit 0
fi
