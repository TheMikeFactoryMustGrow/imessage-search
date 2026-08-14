#!/usr/bin/env python3
"""
mail-search — read-only search of local Apple Mail metadata (Envelope Index).

WHAT: on-demand CLI that reads Mail's SQLite Envelope Index for headers (from, subject,
date, mailbox, read). No daemon, no AppleScript, does NOT launch or control Mail.app,
and never writes the index. Full message bodies live in .emlx files and are out of scope
for this small tool (use Gmail MCP / iCloud web when you need the body).

WHY: agents need local iCloud + Gmail headers without spinning up Mail.app (which can
hang under Automation and under large Envelope Indexes). Same design stance as
imessage-search: direct SQLite, mode=ro + query_only, short busy timeout.

USAGE:
  mail-search recent [limit]                  # newest messages (any mailbox)
  mail-search unread [limit]                  # unread, not deleted
  mail-search text  "<substring>" [limit]     # subject/snippet/from contains
  mail-search from  "<addr/name>" [limit]     # sender address or display name
  mail-search mailboxes                       # list mailboxes with unread counts
  mail-search account "<substr>" [limit]      # filter by mailbox url substring (e.g. icloud, gmail)

Defaults: limit 40. Logging: -v / -vv / -q or MAIL_SEARCH_LOG=DEBUG (stderr only; no
message subjects/bodies in logs).

PREREQUISITE: Full Disk Access for the host process (same as imessage-search).
"""
import os
import re
import sys
import glob
import logging
import sqlite3
from contextlib import closing
from datetime import datetime

HOME = os.path.expanduser("~")
# Prefer newest Mail V* layout; fall back to any Envelope Index under ~/Library/Mail.
_ENVELOPE_CANDIDATES = sorted(
    glob.glob(f"{HOME}/Library/Mail/V*/MailData/Envelope Index"),
    reverse=True,
)
ENVELOPE_PATH = _ENVELOPE_CANDIDATES[0] if _ENVELOPE_CANDIDATES else f"{HOME}/Library/Mail/V10/MailData/Envelope Index"
BUSY_TIMEOUT_MS = 1000

log = logging.getLogger("mail_search")
_LOG_FLAGS = {"-q": logging.ERROR, "--quiet": logging.ERROR,
              "-v": logging.INFO, "--verbose": logging.INFO,
              "-vv": logging.DEBUG, "--debug": logging.DEBUG}

FDA_HINT = (
    "Cannot read Apple Mail's Envelope Index — most likely the app running this lacks "
    "macOS Full Disk Access:\n"
    "  System Settings > Privacy & Security > Full Disk Access > add your terminal/host app,\n"
    "  then fully quit and reopen it.\n"
    f"(Looked for: {ENVELOPE_PATH})"
)


def _resolve_log_level(args):
    level = logging.WARNING
    for tok in list(args):
        if tok in _LOG_FLAGS:
            level = _LOG_FLAGS[tok]
            args.remove(tok)
    env = os.environ.get("MAIL_SEARCH_LOG") or os.environ.get("IMESSAGE_SEARCH_LOG")
    if env:
        level = getattr(logging, env.strip().upper(), level)
    return level


def _setup_logging(level):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    log.handlers[:] = [handler]
    log.setLevel(level)


def open_readonly(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    try:
        con.execute("PRAGMA query_only=ON")
    except sqlite3.Error as exc:  # pragma: no cover
        log.debug("query_only unsupported: %s", exc)
    return con


def parse_limit(v, default=40):
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        sys.exit(f"limit must be a whole number, got: {v!r}")


def connect_envelope():
    try:
        con = open_readonly(ENVELOPE_PATH)
        con.execute("SELECT 1 FROM messages LIMIT 1")
        return con
    except sqlite3.OperationalError as exc:
        log.debug("Envelope Index open/probe failed: %s", exc)
        sys.exit(FDA_HINT)


def fmt_ts(epoch):
    if epoch is None:
        return "????-??-?? ??:??"
    try:
        return datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return str(epoch)


def short_mailbox(url):
    """imap://UUID/INBOX -> account-hint/INBOX (UUID truncated)."""
    if not url:
        return "?"
    # imap://UUID/path or local://UUID/path
    m = re.match(r"^(?:imap|local)://([^/]+)/(.*)$", url)
    if not m:
        return url[:40]
    acct, path = m.group(1), m.group(2)
    hint = acct[:8]
    # common: path is INBOX, [Gmail]/All Mail, etc.
    path = path.replace("%20", " ").replace("%5B", "[").replace("%5D", "]")
    return f"{hint}/{path}"


def display_from(address, comment):
    if comment and address:
        return f"{comment} <{address}>"
    return comment or address or "?"


def sanitize(s):
    if not s:
        return ""
    return "".join(ch if ch == "\t" or ord(ch) >= 32 else " " for ch in s).replace("\n", " ").strip()


# Base projection: one row per message with joined subject / sender / mailbox.
# Note: messages.summary is *not* a reliable subjects-FK for a body preview on modern
# Mail indexes (values collide with unrelated subject strings) — so we omit it.
SELECT_BASE = """
SELECT m.ROWID,
       m.date_received,
       m.read,
       m.deleted,
       mb.url,
       a.address,
       a.comment,
       sub.subject
FROM messages m
LEFT JOIN mailboxes mb ON mb.ROWID = m.mailbox
LEFT JOIN addresses a ON a.ROWID = m.sender
LEFT JOIN subjects sub ON sub.ROWID = m.subject
WHERE COALESCE(m.deleted, 0) = 0
"""


def print_row(row):
    _rowid, date_received, read, _deleted, url, address, comment, subject = row
    flag = " " if read else "U"
    subj = sanitize(subject) or "(no subject)"
    who = sanitize(display_from(address, comment))
    print(f"{fmt_ts(date_received)}  {flag}  {short_mailbox(url):<28.28}  {who:<36.36}  {subj}")


def main():
    args = sys.argv[1:]
    _setup_logging(_resolve_log_level(args))
    if not args:
        sys.exit(__doc__)
    cmd = args[0]
    log.info("run: %s", cmd)

    if cmd == "mailboxes":
        with closing(connect_envelope()) as con:
            rows = con.execute(
                "SELECT ROWID, url, total_count, unread_count FROM mailboxes ORDER BY unread_count DESC, url"
            ).fetchall()
        for rid, url, total, unread in rows:
            print(f"{unread or 0:5d} unread / {total or 0:6d} total  {url}")
        log.info("mailboxes: %d", len(rows))
        return

    with closing(connect_envelope()) as con:
        if cmd == "recent":
            limit = parse_limit(args[1] if len(args) > 1 else None)
            rows = con.execute(
                SELECT_BASE + " ORDER BY m.date_received DESC LIMIT ?", (limit,)
            ).fetchall()
            for r in rows:
                print_row(r)
            log.info("recent: %d", len(rows))
            return

        if cmd == "unread":
            limit = parse_limit(args[1] if len(args) > 1 else None)
            rows = con.execute(
                SELECT_BASE + " AND COALESCE(m.read,0)=0 ORDER BY m.date_received DESC LIMIT ?",
                (limit,),
            ).fetchall()
            for r in rows:
                print_row(r)
            log.info("unread: %d", len(rows))
            return

        if cmd == "text":
            term = args[1] if len(args) > 1 else sys.exit("need a search term")
            limit = parse_limit(args[2] if len(args) > 2 else None)
            like = f"%{term}%"
            rows = con.execute(
                SELECT_BASE + """
                  AND (
                    sub.subject LIKE ? COLLATE NOCASE
                    OR a.address LIKE ? COLLATE NOCASE
                    OR a.comment LIKE ? COLLATE NOCASE
                  )
                  ORDER BY m.date_received DESC LIMIT ?
                """,
                (like, like, like, limit),
            ).fetchall()
            for r in rows:
                print_row(r)
            log.info("text: %d", len(rows))
            return

        if cmd == "from":
            who = args[1] if len(args) > 1 else sys.exit("need a from address/name")
            limit = parse_limit(args[2] if len(args) > 2 else None)
            like = f"%{who}%"
            rows = con.execute(
                SELECT_BASE + """
                  AND (a.address LIKE ? COLLATE NOCASE OR a.comment LIKE ? COLLATE NOCASE)
                  ORDER BY m.date_received DESC LIMIT ?
                """,
                (like, like, limit),
            ).fetchall()
            for r in rows:
                print_row(r)
            log.info("from: %d", len(rows))
            return

        if cmd == "account":
            frag = args[1] if len(args) > 1 else sys.exit("need an account/mailbox substring")
            limit = parse_limit(args[2] if len(args) > 2 else None)
            like = f"%{frag}%"
            rows = con.execute(
                SELECT_BASE + " AND mb.url LIKE ? COLLATE NOCASE ORDER BY m.date_received DESC LIMIT ?",
                (like, limit),
            ).fetchall()
            for r in rows:
                print_row(r)
            log.info("account: %d", len(rows))
            return

    sys.exit(__doc__)


if __name__ == "__main__":
    main()
