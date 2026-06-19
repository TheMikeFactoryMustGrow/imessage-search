# imessage-search

A tiny, read-only CLI that searches your local macOS iMessage/SMS history by reading
`~/Library/Messages/chat.db` directly. Built for use with AI coding agents (Claude Code, etc.)
or straight from your shell. No daemon, no background process, **no send capability**.

## Why
AppleScript-based iMessage tools are fragile — their macOS Automation grants break on Homebrew
`node` upgrades, they need the Messages/Contacts apps running, and they often return raw binary
junk for modern messages. This bypasses all of that with direct SQLite reads, and it correctly
decodes the `attributedBody` blob where macOS (Ventura and later) stores most message text — so a
naive `SELECT text` would silently miss the large majority of recent messages, but this won't.

## Install
```bash
git clone https://github.com/<owner>/imessage-search.git
cd imessage-search
bash install.sh
```
Or, with an AI agent: paste this repo's URL and say **"install this"** — the agent reads
[`AGENTS.md`](AGENTS.md), runs the installer, and tells you the one manual step below.

The installer creates an isolated Python venv (it does **not** touch your system Python),
installs the [`pytypedstream`](https://pypi.org/project/pytypedstream/) parser, and drops a
launcher at `~/.local/bin/imessage-search`.

### One manual step: Full Disk Access
Reading the Messages database requires macOS **Full Disk Access** for whatever app runs the
command (your terminal, or your Claude host). No installer can grant this — it's a privacy
permission you toggle yourself:

> **System Settings → Privacy & Security → Full Disk Access → +** → add your terminal (Terminal /
> iTerm) or Claude host → **fully quit and reopen** that app.

The installer detects whether the grant is in place and tells you if it isn't.

## Usage
```bash
imessage-search recent 20                  # most recent messages, all chats
imessage-search text "dinner plans" 40     # full-text search (newest-first; add --all for full history)
imessage-search handle "+13125551234" 50   # one person's thread
imessage-search contacts "Alex"            # resolve a name -> phone/email
```
Output: `YYYY-MM-DD HH:MM  <name or handle>  <message text>`.

## What it handles correctly
- `attributedBody` typedstream decoding (the modern message-text location)
- Apple's nanoseconds-since-2001 timestamps
- Tapbacks/reactions filtered out
- Handle → contact-name resolution across all AddressBook sources (incl. iCloud)
- WAL-safe read-only access (reflects in-flight messages, never locks the DB)

## Privacy & safety
Read-only and local by design — it cannot send messages and writes nothing. But it can read your
**entire** message history, so treat the output as sensitive: use it with a **local** agent, and
don't pipe your message history to a cloud-hosted model or external service.

## Requirements
macOS, Python 3.9+. That's it.

## Uninstall
```bash
rm -f ~/.local/bin/imessage-search && rm -rf ~/.local/share/imessage-search
```

## License
MIT — see [LICENSE](LICENSE).
