"""Closed-loop battle test: write fake chat.db → run imessage-search → score.

Karpathy autoresearch mapping:
  program.md     → imessage_sim/PROGRAM.md (human instructions)
  prepare.py     → this world builder (agent does not edit)
  train.py       → imessage_search.py (the reader under test)
  val_bpb        → kinds_failed (0 = perfect)
  results.tsv    → stdout TSV (+ optional --out)

No network. No real Messages. No accidental send.
"""
from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import imessage_search as m
from imessage_sim.kinds import KINDS, Kind
from imessage_sim.world import World


def _run_recent(world_path: str, ab_glob: list[str], limit: int = 200) -> str:
    m.CHATDB = f"file:{world_path}?mode=ro"
    m.AB_GLOB = ab_glob
    m.CACHE_DIR = str(Path(world_path).parent / "cache")
    m.CONTACTS_CACHE = str(Path(m.CACHE_DIR) / "c.json")
    buf = io.StringIO()
    argv = sys.argv
    try:
        sys.argv = ["imessage-search", "recent", str(limit)]
        with redirect_stdout(buf):
            m.main()
    finally:
        sys.argv = argv
    return buf.getvalue()


def evaluate_kind(kind: Kind, tmp: Path) -> tuple[bool, str]:
    db = tmp / f"{kind.name}.db"
    w = World(db)
    try:
        kind.build(w)
    finally:
        w.close()
    out = _run_recent(str(db), ab_glob=[])
    missing = [s for s in kind.expect_in_recent if s not in out]
    leaked = [s for s in kind.forbid_in_recent if s in out]
    if missing or leaked:
        detail = []
        if missing:
            detail.append("missing " + ",".join(missing))
        if leaked:
            detail.append("leaked " + ",".join(leaked))
        return False, "; ".join(detail)
    return True, "ok"


def run_all(tmp: Path) -> list[tuple[str, bool, str]]:
    rows = []
    for kind in KINDS:
        ok, detail = evaluate_kind(kind, tmp)
        rows.append((kind.name, ok, detail))
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="iMessage sandbox battle test (no real Messages)")
    p.add_argument("--out", help="write TSV here (default: stdout)")
    p.add_argument("--tmp", help="directory for fake DBs")
    args = p.parse_args(argv)
    tmp = Path(args.tmp) if args.tmp else Path.cwd() / ".imessage-sim-tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    rows = run_all(tmp)
    failed = sum(1 for _, ok, _ in rows if not ok)
    lines = ["kind\tpass\tdetail"]
    for name, ok, detail in rows:
        lines.append(f"{name}\t{int(ok)}\t{detail}")
    lines.append(f"kinds_failed\t{failed}\t0=perfect")
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
