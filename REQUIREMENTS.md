# REQUIREMENTS.md — imessage-search / mail-search / icloud-mail

**Fitness metric:** *Minutes from “agent on this Mac needs mail/iMessage context” to “scoped, usable answer” — without hanging Mail.app, without a standing privileged daemon, and without accidental send.*

Hard-rule changes update this register **in the same change**. Docs and PR text cite IDs; they do not restate the register.

| ID | Requirement | Author | Why (one sentence) | Enforcement |
|----|-------------|--------|--------------------|-------------|
| **R-FIT-01** | Tools optimize for agent + human CLI use on macOS, not a cloud multi-tenant service. | Mike Lingle | The user of record runs local agents (Claude/Grok) next to personal Messages/Mail; cloud connectors for iCloud were already unreliable for that path. | Product scope in README/AGENTS; macOS-only install gate |
| **R-SAFE-01** | iMessage path is **read-only** (no send). | Mike Lingle | Pairing full history read with send is a prompt-injection / accidental-send footgun; drafts/sends for SMS were not the ask. | No send API in `imessage_search.py`; AGENTS forbids it |
| **R-SAFE-02** | Default iCloud capability is **read + draft only**; send requires two gates (`ICLOUD_MAIL_PERMS` includes `send` **and** CLI `--allow-send`). | Mike Lingle | Drafts are reviewable; send is irreversible — default power must be less than “full mail client.” | `require_perm` + `require_send_gates` in `icloud_mail.py`; tests lock both rejects |
| **R-SAFE-03** | Secrets (app-specific password) live only in **macOS Keychain**, never in the repo, chat, or plain config files. | Mike Lingle | Shared agents (Grok + Claude) need one machine-local secret; files get logged, synced, and committed by accident. | `keychain_get`/`keychain_set`; config.json holds **email only** |
| **R-SAFE-04** | Logs must never include message bodies, search terms, credentials, or contact PII. | Mike Lingle | Agents dump stderr into transcripts; operational logs must stay forensic-safe. | Logging contract in modules; tests assert operational messages only |
| **R-SAFE-05** | Do not drive Mail.app or Messages.app via AppleScript/Automation for core paths. | Mike Lingle | Automation grants break on runtime upgrades and hang Mail; direct SQLite/IMAP is the reliability path. | No osascript to those apps; AGENTS forbids the workaround |
| **R-AUTH-01** | iCloud auth uses Apple **App-Specific Password** (not the real Apple ID password); setup opens Apple ID in the browser + macOS dialogs. | Mike Lingle | Apple does not offer OAuth for personal iCloud IMAP; app-specific passwords are the supported third-party model, and GUI setup keeps secrets out of chat. | `icloud-mail setup`; browser + `_osascript_dialog` / TTY fallback |
| **R-AUTH-02** | One setup serves **all agents on that Mac user** (Grok, Claude, Terminal). | Mike Lingle | Re-prompting per agent multiplies secrets and fails; Keychain is the shared OS store. | Single Keychain service `icloud-mail` + `~/.config/icloud-mail/config.json` |
| **R-AUTH-03** | No hardcoded personal email in the shared product; identity comes from setup/env/config. | Mike Lingle | The tool is shareable (e.g. Lindsay); baking one person’s address breaks install for everyone else. | `get_user()`; tests assert no hard default to a personal address |
| **R-DATA-01** | iMessage reads `chat.db` read-only (`mode=ro` + `query_only`) and decodes `attributedBody` typedstream. | Mike Lingle | Since Ventura most bodies are not in `text`; naive SQL silently drops ~98% of recent messages. | `decode_body` + pytypedstream; hermetic blob fixtures |
| **R-DATA-02** | Local Mail tool reads Envelope Index **headers only**, never writes the index. | Mike Lingle | Headers cover triage; full MIME via live IMAP for iCloud; writing Envelope Index risks Mail corruption/hangs. | `mail_search.py` query_only; no UPDATE/INSERT |
| **R-DATA-03** | iCloud bodies/drafts/sends use IMAP/SMTP, not Mail.app. | Mike Lingle | Live bodies and drafts must sync across devices without controlling the GUI. | `icloud_mail.py` imaplib/smtplib |
| **R-REL-01** | Contact name resolution caches under `~/.cache/imessage-search/` and invalidates on AddressBook mtime. | Mike Lingle | Re-scanning multi-MB AddressBook sources on every call is wasteful and increases contention surface. | `load_contacts` cache path + tests |
| **R-REL-02** | Body output is sanitized (control chars / non-characters stripped). | Mike Lingle | Typedstream edge cases emit binary junk that confuses agents and UIs. | `sanitize_body` + tests |
| **R-REL-03** | Long scans may `--snapshot` (copy db+WAL to temp); default `recent` stays live `mode=ro`. | Mike Lingle | Copying a 1.5GB db on every `recent` is slower than WAL readers; snapshot is for isolation on long `text` scans so Messages is never blocked. | `copy_sqlite_snapshot` + `chatdb_conn`; tests |
| **R-REL-04** | `text` search does not fetch `attributedBody` when the `text` column is populated. | Mike Lingle | Most CPU was unarchiving blobs for rows that already had plain text; skip blob I/O on the cheap path. | `msg_select()` CASE expression |
| **R-TEST-03** | A read-only soak (`imessage_sim.soak`) can hammer `recent` in a fake DB (and optionally `--live`) and must report `locked=0`. | Mike Lingle | The failure mode that matters on a real Mac is SQLite busy while Messages writes — not XCTest. | `python -m imessage_sim.soak`; hermetic pytest |
| **R-TEST-01** | Hermetic tests; **100% statement + branch** coverage on logic modules (`fail_under = 100`). | Mike Lingle | Personal mail/message tools are high-blast-radius; untested branches are silent data loss or accidental send. | `.coveragerc`; CI workflow; `pytest` suite |
| **R-TEST-02** | iMessage failure modes are battle-tested in a **fake chat.db sandbox** (tapbacks, attachments, replies, plugins) with no real contacts or Apple send. | Mike Lingle | Reliability here means “decoder+filter survive Apple’s taxonomy,” not “Rust binary.” A closed loop finds leaks without messaging real people. | `imessage_sim/` + `python -m imessage_sim.run`; pytest `test_imessage_sim.py` |
| **R-DIST-01** | Install is one script (`install.sh`) + agent-readable `AGENTS.md` so “paste the repo / install this” works. | Mike Lingle | Non-engineers (and other agents) must not need tribal knowledge to get read/draft working. | `AGENTS.md` playbook; installer verifies FDA + points at `setup` |
| **R-DIST-02** | PR changes to hard rules update this register in the same change; PRs state Accept-if checks. | Mike Lingle | Future cold readers must recover **why** without the original chat context (Elon Phase-1: no authorless requirements). | This file + PR template; submit discipline in AGENTS |

## Explicit non-requirements (deleted / deferred)

| Item | Verdict | Why |
|------|---------|-----|
| Standing MCP daemon holding FDA | DELETE (for this product) | Largest attack surface; on-demand CLI is enough for agent sessions. |
| iMessage send | DELETE | Out of scope; risk >> value for this fitness metric. |
| Full `.emlx` body decode for all Mail accounts | DEFER | iCloud bodies covered by IMAP; multi-account MIME is a second product. |
| OAuth browser login for iCloud | DELETE as approach | Not offered by Apple for IMAP; app-specific password is the real requirement. |
| Separate secrets per agent (Grok vs Claude) | DELETE | Same Mac user should share Keychain; per-agent secrets create drift. |
| Native binary / Rust rewrite | DEFER | Packaging after the reader is proven (R-TEST-02). Rust does not find tapback/decode bugs. |

## Rule-change discipline

Changing any **R-*** row is a product decision: update this file, the enforcing code/tests, and the PR body Accept-if list **together**. If a future session cannot name the author and why for a rule, the rule is not a requirement — delete or rewrite it.
