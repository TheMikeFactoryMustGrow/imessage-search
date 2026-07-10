# AGENTS.md — install & use `imessage-search`

> Instructions for an AI coding agent (Claude Code, etc.). If a user pasted this repo's URL and
> said "install this," follow **Install** below, then tell them the result (especially the Full
> Disk Access step if the verify probe fails).

## What this is
A read-only macOS CLI that searches the user's local iMessage/SMS history by reading
`~/Library/Messages/chat.db` directly. No daemon, no background process, **no send capability**.
It decodes the `attributedBody` typedstream blob (where modern macOS stores most message text),
filters tapbacks, converts Apple's ns-since-2001 timestamps, and resolves handles to contact
names from AddressBook.

## Install
Run from the repo root:
```bash
bash install.sh
```
This is idempotent. It: verifies macOS + `python3`; creates an isolated venv at
`~/.local/share/imessage-search/venv` and installs `pytypedstream` (does **not** touch system
Python — PEP 668-safe); installs a launcher at `~/.local/bin/imessage-search`; then probes the
Messages database and prints the Full Disk Access instructions **if and only if** the probe fails.

## Prerequisite you cannot script: Full Disk Access (FDA)
Reading `chat.db` requires the **host process** (the terminal, e.g. Terminal/iTerm, or the Claude
host app) to have macOS Full Disk Access. This is a TCC permission **no script can grant** — the
user must toggle it by hand:

> System Settings → Privacy & Security → Full Disk Access → **+** → add the terminal/host app →
> **fully quit and reopen** that app (the grant only applies on restart).

**Agent behavior:** if `install.sh`'s verify step (or `imessage-search recent 1`) fails, do not
retry blindly — relay the FDA instruction above to the user, then re-verify after they confirm.

## Verify
```bash
imessage-search recent 3
```
Success = three recent messages print as `YYYY-MM-DD HH:MM  <name/handle>  <text>` with no
`bplist`/`NSKeyedArchiver` junk. If `~/.local/bin` isn't on PATH, the installer prints how to add
it (or call the launcher by full path).

## Usage
```bash
imessage-search recent [limit]                 # most recent messages, all chats
imessage-search text   "<substring>" [limit]   # full-text search of bodies (newest-first; --all = whole history)
imessage-search handle "<phone/email>" [limit] # messages whose handle contains this (use a full number for one person)
imessage-search contacts "<name>"              # resolve a name -> phone/email
```
Defaults: limit 40. `text` scans the newest 80,000 messages unless `--all` is passed.

## Constraints / safety
- macOS only. Read-only. Cannot send messages by design.
- **Local use only.** This exposes the user's entire message history — do not pipe it to a
  cloud-hosted model or external service. Return only the scoped rows the user asked for.
- Output goes to stdout; nothing is written or modified anywhere.

## Uninstall
```bash
rm -f ~/.local/bin/imessage-search
rm -rf ~/.local/share/imessage-search
```
(Optionally revoke the Full Disk Access grant you added.)

<!-- agent-papercuts:begin -->
## Log agent papercuts

When you hit **small friction** while working — a tool call that missed and had
to be retried, a confusing or undocumented setup step, a flaky command, a stale
cache, a misleading error, a non-obvious gotcha — log it via:

```bash
papercut -m <model> "one or two sentences: what you were doing → what got in the way"
```

Data lives under **`.agent-papercuts/`** (open list + history), not a root
`PAPERCUTS.md`, so it does not collide with product files. Do this
**proactively, in the moment**.

Sand: skill **papercuts** ("fix the papercuts"). Clear with `papercut resolve`
(not hand-delete). Long cycle: **papercuts-kaizen**.
<!-- agent-papercuts:end -->
