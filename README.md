# imessage-search · mail-search · icloud-mail

Read-only (by default) macOS CLIs for **iMessage**, **local Apple Mail headers**, and **iCloud email** (IMAP/SMTP). Built for AI agents and the shell.

| Tool | What it talks to | Send? |
|------|------------------|-------|
| `imessage-search` | Local `chat.db` | never |
| `mail-search` | Local Mail Envelope Index (headers) | never |
| `icloud-mail` | iCloud IMAP + SMTP | **gated** (off by default) |

**No AppleScript. No Mail.app automation.** That path is what hangs Mail; these tools avoid it.

## Install

```bash
git clone https://github.com/TheMikeFactoryMustGrow/imessage-search.git
cd imessage-search
bash install.sh
```

### Full Disk Access (local tools)

For `imessage-search` and `mail-search`:

> System Settings → Privacy & Security → Full Disk Access → add Terminal / Claude / Grok host → quit & reopen.

### iCloud setup (any person / any Mac)

```bash
icloud-mail setup
```

This **opens Apple ID in your browser** (sign in → App-Specific Passwords → Generate) and shows
**macOS dialogs** for your iCloud email + that password. Credentials go in Keychain +
`~/.config/icloud-mail/config.json` — never in the repo.

Sharing: paste the repo into Claude or Grok and say **“install this”** — agents follow
[`AGENTS.md`](AGENTS.md) end-to-end (including the browser setup for whoever is using the Mac).

## Permissions (iCloud)

| Mode | Default | Commands |
|------|---------|----------|
| `read` | on | folders, recent, unread, search, read, drafts, auth-check |
| `draft` | on | draft (IMAP APPEND to Drafts — syncs to all devices) |
| `send` | **off** | send, send-draft |

Send requires **two gates**:

```bash
export ICLOUD_MAIL_PERMS=read,draft,send   # gate 1: policy
icloud-mail send --to a@b.com --subject "Hi" --body "Hello" --allow-send   # gate 2: explicit flag
```

Without both, send refuses. Check with `icloud-mail perms`.

## Usage

### iMessage

```bash
imessage-search recent 20
imessage-search text "dinner" 40
imessage-search handle "+13125551234" 50
imessage-search unread 25 14
imessage-search contacts "Alex"
```

### Local Mail headers (any account Mail has synced)

```bash
mail-search recent 20
mail-search unread 25
mail-search text "invoice" 40
mail-search from "stilo" 20
mail-search account INBOX 30
mail-search mailboxes
```

### iCloud live mail (bodies + draft + optional send)

```bash
icloud-mail auth-check
icloud-mail folders
icloud-mail recent 20
icloud-mail unread 25
icloud-mail search "invoice" 40
icloud-mail read <uid>                 # full body
icloud-mail drafts 20
icloud-mail draft --to a@b.com --subject "Hi" --body "Hello"
# optional:
# icloud-mail draft --to a@b.com --subject "Hi" --body-file ./msg.txt
# ICLOUD_MAIL_PERMS=read,draft,send icloud-mail send --to a@b.com --subject "Hi" --body "Hello" --allow-send
# ICLOUD_MAIL_PERMS=read,draft,send icloud-mail send-draft <uid> --allow-send
```

## Reliability

- **iMessage / local Mail:** SQLite `mode=ro` + `query_only` + short `busy_timeout`; contact index cached under `~/.cache/imessage-search/`.
- **iCloud:** stdlib `imaplib` / `smtplib` only; password only via Keychain (`security`); no node MCP required.
- Logs (`-v` / `-vv`) never print message bodies, search terms, or passwords.

## Privacy

Local tools can expose full history — use with a **local** agent. Treat iCloud drafts/sends as real mail.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/coverage run -m pytest && .venv/bin/coverage report -m
```

100% line + branch coverage enforced.

### iMessage sandbox (no real people, no Apple send)

Fake Messages DB you can write into, then read with the CLI. Tapbacks, attachments, replies, **edits**, **unsends**, stickers, plugins:

```bash
python3 -m imessage_sim.run
# metric: kinds_failed  (0 = every kind passed)
```

See `imessage_sim/PROGRAM.md` for the experiment loop.

## Uninstall

```bash
rm -f ~/.local/bin/imessage-search ~/.local/bin/mail-search ~/.local/bin/icloud-mail
rm -rf ~/.local/share/imessage-search ~/.cache/imessage-search
# optional: security delete-generic-password -a "$USER" -s icloud-mail
```

## License

MIT — see [LICENSE](LICENSE).
