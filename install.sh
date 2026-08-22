#!/usr/bin/env bash
# imessage-search + mail-search + icloud-mail installer — idempotent.
set -euo pipefail

APP="imessage-search"
SRC="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/$APP"
BIN="$HOME/.local/bin"
VENV="$DATA/venv"

echo "==> Installing $APP (+ mail-search, icloud-mail)"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "ERROR: macOS only." >&2; exit 1
fi
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found." >&2; exit 1; }

mkdir -p "$DATA" "$BIN"
cp "$SRC/imessage_search.py" "$DATA/imessage_search.py"
cp "$SRC/mail_search.py" "$DATA/mail_search.py"
cp "$SRC/icloud_mail.py" "$DATA/icloud_mail.py"
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
if ! "$VENV/bin/pip" install --quiet pytypedstream >/dev/null; then
  echo "ERROR: could not install pytypedstream. Restore network and re-run." >&2
  exit 1
fi
echo "    venv + pytypedstream ready at $VENV"

install_launcher() {
  local name="$1" script="$2"
  cat > "$BIN/$name" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python" "$DATA/$script" "\$@"
EOF
  chmod +x "$BIN/$name"
  echo "    launcher installed at $BIN/$name"
}

install_launcher imessage-search imessage_search.py
install_launcher mail-search mail_search.py
install_launcher icloud-mail icloud_mail.py

case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "    NOTE: $BIN is not on your PATH. Add: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo "==> Verifying local indexes (Full Disk Access) ..."
ok_msg=0; ok_mail=0
if "$BIN/imessage-search" recent 1 >/dev/null 2>&1; then ok_msg=1; echo "    OK — Messages chat.db"; else echo "    WARN — Messages chat.db not readable (FDA?)"; fi
if "$BIN/mail-search" recent 1 >/dev/null 2>&1; then ok_mail=1; echo "    OK — Mail Envelope Index"; else echo "    WARN — Envelope Index not readable (FDA?)"; fi

echo "==> iCloud IMAP auth ..."
if "$BIN/icloud-mail" auth-check >/dev/null 2>&1; then
  echo "    OK — iCloud IMAP login (Keychain + config present)"
else
  echo "    NEED SETUP (once per person / Mac):"
  echo "      icloud-mail setup"
  echo "      → opens Apple ID in your browser (sign in + create App-Specific Password)"
  echo "      → macOS dialogs collect your iCloud email + that password"
  echo "      → stores Keychain credentials and verifies login"
fi

echo ""
echo "Share / agent install: paste this repo + AGENTS.md into Claude or Grok and say \"install this\"."
echo "Defaults: iCloud send is OFF (read+draft only)."
echo "  export ICLOUD_MAIL_PERMS=read,draft,send   # unlock send"
echo "  icloud-mail send ... --allow-send            # second gate"
echo ""
echo "Try:  icloud-mail setup          # first time"
echo "      icloud-mail unread 5"
echo "      imessage-search recent 5"
echo "      mail-search unread 10"
exit 0
