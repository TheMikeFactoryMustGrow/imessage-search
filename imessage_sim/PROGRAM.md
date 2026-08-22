# program.md — iMessage sandbox (Karpathy autoresearch shape)

You are iterating on **read correctness + speed**, not on sending real iMessages.

## Goal

A battle-tested, fast, reliable, **read-only** iMessage CLI:

- `kinds_failed = 0` from `python -m imessage_sim.run`
- soak: `python -m imessage_sim.soak --rounds 20` → `locked=0` (hermetic)  
  optional live: `--live --rounds 100` on a Mac with FDA (still never sends)
- `pytest` + 100% coverage stays green

No network. No `~/Library/Messages` writes. No accidental SMS.

## Fitness metrics (keep all green)

| Metric | Command | Perfect |
|--------|---------|---------|
| `kinds_failed` | `python -m imessage_sim.run` | **0** |
| soak `locked` | `python -m imessage_sim.soak --rounds 20` | **0** |
| coverage | `coverage run -m pytest && coverage report` | **100%** stmt+branch |

Do not trade one metric for another (e.g. dropping a Kind to make soak faster).

## What you may edit

- `imessage_search.py` — reader (decoder, filters, snapshot, text scan).
- `imessage_sim/kinds.py` — add a Kind when you discover a real Messages row type the catalog misses.
- Tests that lock the new kind or soak behavior.

## What you must not edit

- `imessage_sim/world.py` schema without a failing kind that requires it.
- Anything that talks to Apple, IMAP, or real contacts.
- **No send / no AppleScript / no loosening `--allow-send`.**

## Loop (one experiment)

1. Read this file, `imessage_sim/kinds.py`, and recent `results.tsv` if present.
2. Pick **one** hypothesis (example: “type=1000 stickers leak U+FFFC into recent”).
3. If the kind is missing, add it with `expect_in_recent` / `forbid_in_recent`.
4. Run: `python -m imessage_sim.run --tmp /tmp/imsg-sim`
5. Run: `coverage run -m pytest -q && coverage report`
6. If `kinds_failed` increased because the reader is wrong: **fix the reader**, re-run.
7. If metrics still perfect: keep the new kind (locked regression). Do not loosen forbids.
8. Append one TSV line to `imessage_sim/results.tsv` (create if needed):

   `timestamp\thypothesis\tkinds_failed\tpytest_ok\tnote`

## Hard rules (REQUIREMENTS.md)

- R-SAFE-01: no send. `World.post` / `edit` / `unsend` only mutate a temp DB.
- Edited → current text + ` (edited)`. Unsent (`date_retracted`) never leaks original body.
- R-REL-03: `--snapshot` copies db+WAL so long scans do not sit on the live file; default `recent` stays live `mode=ro`.
- R-REL-04: `text` does not load `attributedBody` when the `text` column is present.
- R-TEST-01: 100% coverage. R-TEST-02: sandbox kinds. R-TEST-03: soak locked=0.

## Why not Rust / Xcode

This loop attacks decoder/filter/locking bugs. A binary does not add kinds. Xcode cannot fake Apple’s chat.db taxonomy for us.
