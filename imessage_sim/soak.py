"""Live-read soak: many `recent` calls, no send.

Hermetic default uses a fake World. Pass --live to hit ~/Library/Messages
(read-only) while Messages.app may be writing — that's the race we care about.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

import imessage_search as m
from imessage_sim.world import World


def _recent_once(limit: int = 20) -> None:
    argv = sys.argv
    buf = io.StringIO()
    try:
        sys.argv = ["imessage-search", "recent", str(limit)]
        with redirect_stdout(buf):
            m.main()
    finally:
        sys.argv = argv


def soak(*, rounds: int, live: bool, tmp: Path | None, snapshot: bool) -> dict:
    saved = None
    if not live:
        tmp = tmp or Path.cwd() / ".imessage-sim-tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        db = tmp / "soak.db"
        w = World(db)
        try:
            for i in range(30):
                w.post(handle="+15550001111", from_me=bool(i % 2), text=f"soak {i}", days_ago=i / 100)
        finally:
            w.close()
        saved = (m.CHATDB, list(m.AB_GLOB), m.CACHE_DIR, m.CONTACTS_CACHE)
        m.CHATDB = f"file:{db}?mode=ro"
        m.AB_GLOB = []
        m.CACHE_DIR = str(tmp / "cache")
        m.CONTACTS_CACHE = str(tmp / "cache" / "c.json")
    times = []
    locked = 0
    other = 0
    old_snap = os.environ.get("IMESSAGE_SEARCH_SNAPSHOT")
    if snapshot:
        os.environ["IMESSAGE_SEARCH_SNAPSHOT"] = "1"
    try:
        for _ in range(rounds):
            t0 = time.perf_counter()
            try:
                _recent_once(20)
            except SystemExit as exc:
                msg = str(exc)
                if "locked" in msg.lower() or "busy" in msg.lower():
                    locked += 1
                elif msg and msg != "None":
                    other += 1
            times.append(time.perf_counter() - t0)
    finally:
        if snapshot:
            if old_snap is None:
                os.environ.pop("IMESSAGE_SEARCH_SNAPSHOT", None)
            else:
                os.environ["IMESSAGE_SEARCH_SNAPSHOT"] = old_snap
        if saved is not None:
            m.CHATDB, m.AB_GLOB, m.CACHE_DIR, m.CONTACTS_CACHE = saved
    times.sort()
    n = len(times) or 1
    def pct(p):
        i = min(n - 1, max(0, int(round((p / 100) * (n - 1)))))
        return times[i]
    return {
        "rounds": rounds,
        "locked": locked,
        "other_errors": other,
        "p50": pct(50),
        "p95": pct(95),
        "p99": pct(99),
        "max": times[-1] if times else 0.0,
        "ok": locked == 0 and other == 0,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Soak imessage-search recent (read-only)")
    p.add_argument("--rounds", type=int, default=20)
    p.add_argument("--live", action="store_true", help="use real chat.db (still read-only, never send)")
    p.add_argument("--snapshot", action="store_true")
    p.add_argument("--tmp")
    args = p.parse_args(argv)
    if args.rounds < 1:
        sys.exit("rounds must be >= 1")
    stats = soak(
        rounds=args.rounds,
        live=args.live,
        tmp=Path(args.tmp) if args.tmp else None,
        snapshot=args.snapshot,
    )
    print(
        f"rounds={stats['rounds']} locked={stats['locked']} other={stats['other_errors']} "
        f"p50={stats['p50']:.4f}s p95={stats['p95']:.4f}s p99={stats['p99']:.4f}s max={stats['max']:.4f}s"
    )
    return 0 if stats["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
