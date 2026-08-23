# AGENTS.md — install & set up mail + iMessage tools (for any Mac user)

**Trigger:** someone pasted this repo URL (or path) into Claude / Grok / another coding agent
and said any of: **install this**, **set this up**, **give me iMessage/iCloud mail**, or
**share this with me**.

**You are the installer.** Run the steps below yourself. Talk to the user in plain language.
Do **not** invent OAuth for iCloud — Apple only supports **App-Specific Passwords** for IMAP;
this tool opens the Apple ID **browser** and a **macOS dialog** so setup feels like a login flow.

**Hard product rules** (author + why): see [`REQUIREMENTS.md`](REQUIREMENTS.md). Cite IDs (e.g. R-SAFE-02)
when changing behavior. Prefer `icloud-mail auth-check` before `setup` if the machine may already be configured.

---

## 0. What you are installing

| CLI | Purpose | Needs |
|-----|---------|--------|
| `imessage-search` | Search local iMessage/SMS history | Full Disk Access |
| `mail-search` | Search local Apple Mail **headers** (any account Mail synced) | Full Disk Access |
| `icloud-mail` | **Read / draft / (optional) send** live iCloud mail via IMAP+SMTP | App-Specific Password in Keychain |

- **No AppleScript.** Never control Mail.app or Messages.app with Automation (that hangs them).
- **Send is OFF by default** for iCloud (`read` + `draft` only). Never enable send unless the user explicitly asks to send a message.
- macOS only. Python 3.9+.

Repo (canonical): `https://github.com/TheMikeFactoryMustGrow/imessage-search`

---

## 1. Install (you run this)

```bash
# If not already cloned:
git clone https://github.com/TheMikeFactoryMustGrow/imessage-search.git
cd imessage-search

bash install.sh
```

Idempotent. Creates:

- `~/.local/share/imessage-search/` (venv + scripts)
- `~/.local/bin/imessage-search`
- `~/.local/bin/mail-search`
- `~/.local/bin/icloud-mail`

Ensure `~/.local/bin` is on `PATH` (installer prints the line to add to `~/.zshrc` if not).

**Agent check:** `command -v icloud-mail && icloud-mail perms`

---

## 2. Full Disk Access (local iMessage + Mail index)

Only needed for `imessage-search` and `mail-search` (not for live iCloud IMAP).

If `imessage-search recent 1` or `mail-search recent 1` fails:

1. Tell the user (do not loop): open  
   **System Settings → Privacy & Security → Full Disk Access**
2. Click **+** and add the app that runs the agent (Terminal, iTerm, **Claude**, **Grok**, etc.)
3. **Fully quit and reopen** that app (grant applies on restart)
4. Re-run the verify commands

---

## 3. iCloud setup for *this* person (browser + macOS dialogs)

This is the path Lindsay (or anyone) needs. **Do not hardcode Mike’s email.**

### 3a. Run the guided wizard

```bash
icloud-mail setup
```

What happens (expected UX):

1. **Browser opens** to [appleid.apple.com](https://appleid.apple.com/account/manage)
2. User **signs in** with their Apple ID in the browser
3. User goes to **Sign-In and Security → App-Specific Passwords → Generate**  
   (label e.g. `icloud-mail`; copy the `xxxx-xxxx-xxxx-xxxx` password)  
   Help: https://support.apple.com/en-us/HT204397
4. A **macOS dialog** asks for:
   - their **iCloud email** (e.g. `lindsay@icloud.com`)
   - the **App-Specific Password** (hidden field)
5. Tool stores them in the **login Keychain** + `~/.config/icloud-mail/config.json`
6. Tool **verifies IMAP login** and prints OK

**What you tell the user before running setup:**

> I’m starting iCloud mail setup. Your browser will open to Apple ID — sign in and create an
> App-Specific Password (not your normal password). Then paste that password into the Mac
> dialog that appears, along with your iCloud email.

**Agent rules for setup:**

- Run `icloud-mail setup` in a context where GUI dialogs work (local Mac session — not a headless remote sandbox).
- If the agent host has no GUI, tell the user to open **Terminal** and run `icloud-mail setup` themselves, then continue when they say done.
- Never ask the user to paste the app-specific password into the **chat** (it would land in logs). Always use the setup dialog / Keychain path.
- Optional non-interactive email: `icloud-mail setup --user their@icloud.com` (still opens browser + password dialog).
- Skip browser only if they already have the password ready: `icloud-mail setup --no-browser`

### 3b. Verify iCloud

```bash
icloud-mail auth-check
icloud-mail unread 5
```

Success = dated lines with subjects, no Keychain errors.

---

## 4. Day-to-day usage (after setup)

### iMessage

```bash
imessage-search recent 20
imessage-search text "dinner" 40
imessage-search handle "+1…" 50
imessage-search contacts "Name"
```

### Local Mail headers (fast; no IMAP)

```bash
mail-search recent 20
mail-search unread 25
mail-search text "invoice" 40
```

### Live iCloud (bodies + drafts)

```bash
icloud-mail folders
icloud-mail recent 20
icloud-mail unread 25
icloud-mail search "keyword" 40
icloud-mail read <uid>
icloud-mail drafts
icloud-mail draft --to "person@example.com" --subject "Hi" --body "Hello"
```

### Send (only if the user explicitly asks to send)

Two gates — both required:

```bash
export ICLOUD_MAIL_PERMS=read,draft,send
icloud-mail send --to "…" --subject "…" --body "…" --allow-send
# or: icloud-mail send-draft <uid> --allow-send
```

Default perms are **read,draft only**. Check with `icloud-mail perms`.  
**Agents must not set `ICLOUD_MAIL_PERMS=…send` or pass `--allow-send` unless the user clearly requested sending.**

---

## 5. What “done” looks like (report this to the user)

- [ ] `install.sh` succeeded; three launchers on PATH  
- [ ] (Optional) FDA granted if they want local iMessage/Mail index  
- [ ] `icloud-mail setup` completed for **their** email  
- [ ] `icloud-mail auth-check` → OK  
- [ ] Remind: **draft is default; send needs an extra yes from them**

Example closing message:

> Installed. iCloud is signed in as **you@icloud.com**. I can read mail and save drafts.
> I will not send anything unless you explicitly ask. Try: “show my unread iCloud mail”.

---

## 6. Safety & privacy

- Passwords only in **Keychain** (`service=icloud-mail`). Never commit secrets; never print them.
- Do not dump full mail/message history into a cloud model unless the user asked for that scope.
- stdout = results; stderr = logs. Logs never include bodies or credentials.
- Do **not** use `osascript` to drive Mail/Messages apps as a workaround.

---

## 7. Uninstall

```bash
rm -f ~/.local/bin/imessage-search ~/.local/bin/mail-search ~/.local/bin/icloud-mail
rm -rf ~/.local/share/imessage-search ~/.cache/imessage-search ~/.config/icloud-mail
# optional Keychain cleanup (replace EMAIL):
# security delete-generic-password -a "EMAIL" -s icloud-mail
```

---

## 8. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `chat.db` / Envelope Index unreadable | Full Disk Access + quit/reopen host app |
| `no iCloud user configured` | `icloud-mail setup` |
| `no iCloud mail password in Keychain` | `icloud-mail setup` or `auth-set` |
| IMAP login failed after setup | Wrong password type (must be **App-Specific**, not Apple ID password); re-run setup |
| Dialog never appears | Run `icloud-mail setup` in local Terminal (GUI session) |
| Send refused | Expected unless user asked to send + both gates set |
| Agent is remote/cloud without Mac GUI | iCloud setup must run on the Mac; local iMessage/Mail index also require the Mac |

---

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

Entries are repo files: **commit them, and they ride the session's PR** —
include `.agent-papercuts/` changes in the branch you push. An entry logged in
an ephemeral container but never pushed is lost with the container.

Sand: skill **papercuts** ("fix the papercuts"). Clear with `papercut resolve`
(not hand-delete). Long cycle: **papercuts-kaizen**.

No `papercut` on PATH? One-time machine install (CLI → `~/.local/bin`):
`git clone https://github.com/TheMikeFactoryMustGrow/papercuts && papercuts/scripts/papercut install`

Cannot install (restricted machine, cloud container)? The format is the tool — append by hand:
if `.agent-papercuts/open.md` does not exist, start it with the ownership marker line
`<!-- agent-papercuts:v1 -->` (without it the CLI treats the file as foreign and refuses it later);
then append `<UTC ISO-8601 stamp> - <model> - <author>`, a blank line, a one-paragraph body
(`<model>` and `<author>` are single tokens, no spaces — `jane-doe`, not `Jane Doe`).
`.agent-papercuts/history.jsonl` gets ONE physical line per entry —
`{"event": "logged", "entry_stamp": "<stamp>", "model": "…", "author": "…", "body": "…", "ts": "<stamp>", "repo_root": "<abs git root>"}`
— string values JSON-escaped (quotes, backslashes, newlines), or the line silently drops from history views.
<!-- agent-papercuts:end -->

## Submit discipline (R-DIST-02)

Draft = still working. **Ready-for-review is the done-signal**, flipped by the
authoring session itself the moment the PR's Accept-if checks pass (CI green
where CI applies) — never left to a human. Verified work sitting in draft
stalls the merge loop.
