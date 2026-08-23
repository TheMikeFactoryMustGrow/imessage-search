<!--
  Agents and humans: fill every section that applies.
  Reviewers evaluate the *reasoning*, not only the diff.
  Hard rules: REQUIREMENTS.md (cite IDs).
-->

## Summary

<!-- 1–3 bullets: what changed in concrete terms -->

-

**Supersedes / relates to:** <!-- PR #s, RCAs, rulings that led here — "none" if standalone -->

## Problem / opportunity

<!-- What was wrong, missing, or costly *before* this PR? Who felt it? -->



## First principles

| Principle | How this PR honors it |
| --- | --- |
| Fitness: minutes to scoped mail/iMessage context without hanging Mail | |
| REQUIREMENTS.md rows have named author + why (cite R-*) | |
| Prefer on-demand CLI over standing daemons | |
| Default power < full mail client (read+draft; send gated) | |

### Alternatives considered

-

### Non-goals

-

## Requirements touched

<!-- REQUIREMENTS.md IDs this PR affects, adds, or relies on — "none" if none. Hard-rule changes update the register in the same PR. -->

-

## How to evaluate this update

**Accept if:**

- [ ] `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/coverage run -m pytest && .venv/bin/coverage report` shows **100%** statements + branches (`fail_under = 100`)
- [ ] No secrets (app-specific passwords, tokens) in the diff
- [ ] REQUIREMENTS.md updated if any hard rule changed
- [ ] AGENTS.md still supports “paste repo → install this” for a cold Mac
- [ ] Send remains dual-gated unless this PR intentionally changes R-SAFE-02 (with why)

**Reject if:**

- [ ] Coverage drops below 100% without a named exclusion + reason
- [ ] AppleScript-to-Mail/Messages is reintroduced as a core path
- [ ] Default send is enabled without dual gate
- [ ] Personal email hardcodes return for shared install

**Manual / scripted checks run:**

```text
# commands run + results
```

## Impact surface

- [ ] `imessage_search.py` (local Messages)
- [ ] `mail_search.py` (local Envelope Index)
- [ ] `icloud_mail.py` (IMAP/SMTP)
- [ ] `install.sh` / launchers
- [ ] `AGENTS.md` / `README.md` / `REQUIREMENTS.md`
- [ ] Tests / CI

**Blast radius (one line):**

## Risk & rollback

-

## Test plan

-

## Agent notes

<!-- Cold-reader context: anything a future agent needs that is not in REQUIREMENTS.md -->

-
