# program.md — iMessage sandbox (Karpathy autoresearch shape)

You are iterating on **read correctness**, not on sending real iMessages.

## Fitness metric

`kinds_failed` from `python -m imessage_sim.run` — **0 is perfect**. Every kind is a closed loop: write a fake `chat.db` → `imessage-search recent` → assert expect/forbid substrings.

No network. No `~/Library/Messages`. No accidental SMS.

## What you may edit

- `imessage_search.py` — the reader (decoder, filters, format).
- `imessage_sim/kinds.py` — add a Kind when you discover a real Messages row type the catalog misses.
- Tests that lock the new kind.

## What you must not edit

- `imessage_sim/world.py` schema without a failing kind that requires it.
- Anything that talks to Apple, IMAP, or real contacts.

## Loop (one experiment)

1. Read this file and `imessage_sim/kinds.py`.
2. Pick **one** hypothesis (example: “type=1000 stickers leak U+FFFC into recent”).
3. If the kind is missing, add it with `expect_in_recent` / `forbid_in_recent`.
4. Run: `python -m imessage_sim.run --tmp /tmp/imsg-sim`
5. If `kinds_failed` increased because the reader is wrong: **fix the reader**, re-run.
6. If `kinds_failed` is still 0: keep the new kind (it’s a locked regression). Do not “improve” by loosening forbids.
7. Log one line: hypothesis, change, `kinds_failed` before/after.

## Hard rules (REQUIREMENTS.md)

- R-SAFE-01: this tool does not send. The sandbox `World.post(..., from_me=True)` only INSERTs into a temp DB.
- R-SAFE-05: no AppleScript.
- R-TEST-01: hermetic pytest still 100% coverage.

## Why this is the reliability step (not Rust)

Rust would prevent memory bugs in a rewrite. It would not tell you whether tapback type 2006 leaks into `recent`. This loop attacks **the actual failure mode**: wrong decode / wrong filter on Apple’s message taxonomy.
