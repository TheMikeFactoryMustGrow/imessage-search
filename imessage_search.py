#!/usr/bin/env python3
"""
imessage-search — read-only search of local iMessage history (~/Library/Messages/chat.db).

WHAT: a tiny, on-demand CLI that reads your Messages database directly (read-only) so an
AI agent or you can search your iMessage/SMS history. No daemon, no background process,
NO send capability. macOS only. Does NOT open Messages.app or Mail.app.

WHY IT EXISTS: AppleScript-based iMessage tools are fragile (TCC Automation grants break on
Homebrew node upgrades, the Contacts app must be running, etc.). This bypasses AppleEvents
entirely with direct SQLite reads — robust and fast.

CORRECTNESS: since macOS Ventura, most message text lives in the `attributedBody` BLOB (a NeXT
`typedstream` archive), not the `text` column. A naive `SELECT text` silently misses the large
majority of recent messages. This tool decodes the blob with a real typedstream parser
(pytypedstream). It also handles ns-since-2001 dates, filters tapbacks/reactions, and resolves
handles to contact names across all AddressBook sources.

RELIABILITY:
  - Opens SQLite with URI mode=ro + PRAGMA query_only (never writes; writers keep the lock).
  - Caches the contact index under ~/.cache/imessage-search (invalidates on AddressBook mtime).
  - Sanitizes control characters in bodies so agents never see binary junk.
  - Always closes DB handles; busy_timeout avoids brief writer races without spinning.

USAGE:
  imessage-search text   "<substring>" [limit] [--all]   # search message bodies (recent-first)
  imessage-search handle "<phone/email>" [limit]         # all messages with one handle
  imessage-search recent [limit]                         # most recent messages, all chats
  imessage-search unread [limit] [days]                  # recent unread incoming (default 25, last 14 days)
  imessage-search chat   "<chat id / room>" [limit]      # messages in a group thread (by chat_identifier)
  imessage-search contacts "<name>"                      # name -> phone/email (all sources)
Defaults: limit 40. `text` scans newest-first up to 80000 rows; pass --all to scan everything.

LOGGING: add -v (info) / -vv (debug) / -q (errors only), or set IMESSAGE_SEARCH_LOG=DEBUG.
Logs go to stderr (results stay on stdout) and NEVER include message text, search terms,
or contact details — only operational facts (commands, counts, byte sizes, errors).

PREREQUISITE: the process running this needs macOS Full Disk Access (System Settings >
Privacy & Security > Full Disk Access). See README / AGENTS.md.
"""
import json
import sqlite3
import os
import sys
import glob
import re
import time
import logging
from contextlib import closing

HOME = os.path.expanduser("~")
CHATDB_PATH = f"{HOME}/Library/Messages/chat.db"
CHATDB = f"file:{CHATDB_PATH}?mode=ro"   # ro: WAL-safe, reflects in-flight messages
AB_GLOB = [f"{HOME}/Library/Application Support/AddressBook/AddressBook-v22.abcddb",
           *glob.glob(f"{HOME}/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb")]
OBJ_REPLACEMENT = "￼"  # attachment placeholder char
TEXT_SCAN_CAP = 80000  # `text` decodes bodies in Python as it scans; cap to newest-N unless --all
CACHE_DIR = os.path.join(HOME, ".cache", "imessage-search")
CONTACTS_CACHE = os.path.join(CACHE_DIR, "contacts-v1.json")
BUSY_TIMEOUT_MS = 1000

try:
    import typedstream
except ImportError:  # pragma: no cover - only when the venv is missing the parser
    sys.exit("typedstream parser missing — re-run install.sh (it builds the venv with pytypedstream).")

# Operational logging only — NEVER message bodies, search terms, or contact PII. stderr, not stdout.
log = logging.getLogger("imessage_search")
_LOG_FLAGS = {"-q": logging.ERROR, "--quiet": logging.ERROR,
              "-v": logging.INFO, "--verbose": logging.INFO,
              "-vv": logging.DEBUG, "--debug": logging.DEBUG}


def _resolve_log_level(args):
    """Pop any logging flags from args (in place); return the level. IMESSAGE_SEARCH_LOG env wins."""
    level = logging.WARNING
    for tok in list(args):
        if tok in _LOG_FLAGS:
            level = _LOG_FLAGS[tok]
            args.remove(tok)
    env = os.environ.get("IMESSAGE_SEARCH_LOG")
    if env:
        level = getattr(logging, env.strip().upper(), level)
    return level


def _setup_logging(level):
    handler = logging.StreamHandler(sys.stderr)            # stdout stays reserved for results
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    log.handlers[:] = [handler]                            # idempotent across calls
    log.setLevel(level)


def open_readonly(uri_or_path, *, uri=True):
    """Open a SQLite database for pure reads. Never writes; short busy timeout."""
    con = sqlite3.connect(uri_or_path, uri=uri)
    con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    try:
        con.execute("PRAGMA query_only=ON")
    except sqlite3.Error as exc:  # pragma: no cover - very old SQLite
        log.debug("query_only unsupported: %s", exc)
    return con


def _collect_strings(obj, depth=0, found=None):
    """Recursively gather every string in an unarchived typedstream object graph."""
    if found is None:
        found = []
    if depth > 6:                        # defensive recursion guard (cyclic/pathological graphs)
        return found
    if isinstance(obj, str):
        found.append(obj)
    elif isinstance(obj, (list, tuple)):
        for i in obj:
            _collect_strings(i, depth + 1, found)
    else:
        for attr in ("value", "values", "contents"):
            if hasattr(obj, attr):
                _collect_strings(getattr(obj, attr), depth + 1, found)
    return found


def sanitize_body(s):
    """Strip control chars and attachment placeholders; keep readable text only."""
    if s is None:
        return None
    if s == "":
        return None
    s = s.replace(OBJ_REPLACEMENT, "")
    # Drop C0 controls (except tab) and unpaired surrogates / non-characters that leak from blobs.
    cleaned = "".join(
        ch for ch in s
        if ch == "\t" or (ord(ch) >= 32 and ord(ch) not in (0xFFFE, 0xFFFF) and not (0xD800 <= ord(ch) <= 0xDFFF))
    )
    cleaned = cleaned.strip()
    return cleaned or None


def decode_body(text, blob):
    """Return the human-readable body: prefer the plain text column, else decode attributedBody."""
    if text:
        cleaned = sanitize_body(text)
        return cleaned if cleaned else "[attachment]"
    if not blob:
        return None
    try:
        obj = typedstream.unarchive_from_data(blob)
    except Exception as exc:
        log.debug("attributedBody decode failed (%d bytes): %s", len(blob), exc)
        return None
    for s in _collect_strings(obj):      # NSAttributedString stores the body as the first string
        cleaned = sanitize_body(s)
        if cleaned:
            return cleaned
    return "[attachment]"                # decoded, but no usable text (e.g. attachment-only)


def apple_ts(date):
    """message.date is ns-since-2001 on modern macOS, seconds on very old; detect by magnitude."""
    secs = date / 1_000_000_000 if date > 1_000_000_000_000 else date
    return secs + 978307200  # 2001-01-01 -> Unix epoch


def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 10 else d


def _ab_mtimes():
    """Stable map of AddressBook path -> mtime for cache invalidation."""
    out = {}
    for db in AB_GLOB:
        try:
            out[db] = os.path.getmtime(db)
        except OSError:
            continue
    return out


def _load_contacts_uncached():
    """Map normalized phone (last 10 digits) and lowercased email -> contact name, across all sources."""
    names = {}
    opened = 0
    for db in AB_GLOB:
        if not os.path.exists(db):
            continue
        try:
            with closing(open_readonly(f"file:{db}?mode=ro", uri=True)) as con:
                for first, last, phone, email in con.execute("""
                    SELECT r.ZFIRSTNAME, r.ZLASTNAME, p.ZFULLNUMBER, e.ZADDRESS
                    FROM ZABCDRECORD r
                    LEFT JOIN ZABCDPHONENUMBER p ON p.ZOWNER = r.Z_PK
                    LEFT JOIN ZABCDEMAILADDRESS e ON e.ZOWNER = r.Z_PK"""):
                    name = (f"{first or ''} {last or ''}").strip()
                    if not name:
                        continue
                    if phone:
                        names.setdefault(norm_phone(phone), name)
                    if email:
                        names.setdefault(email.strip().lower(), name)
            opened += 1
        except sqlite3.Error as exc:
            log.debug("AddressBook source unreadable (%s): %s", db, exc)
            continue
    log.info("contacts index: %d key(s) from %d of %d source(s)", len(names), opened, len(AB_GLOB))
    return names, opened


def load_contacts(*, use_cache=True):
    """Load handle→name map. Cached under ~/.cache; invalidated when any AddressBook source mtime changes."""
    if os.environ.get("IMESSAGE_SEARCH_NO_CONTACTS") == "1":
        log.info("contacts index: skipped (IMESSAGE_SEARCH_NO_CONTACTS=1)")
        return {}
    mtimes = _ab_mtimes()
    if use_cache and mtimes and os.path.exists(CONTACTS_CACHE):
        try:
            with open(CONTACTS_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("mtimes") == mtimes and isinstance(cached.get("names"), dict):
                log.info("contacts index: %d key(s) from cache", len(cached["names"]))
                return cached["names"]
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            log.debug("contacts cache unreadable: %s", exc)
    names, opened = _load_contacts_uncached()
    if use_cache and opened > 0:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = CONTACTS_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"mtimes": mtimes, "names": names}, f, separators=(",", ":"))
            os.replace(tmp, CONTACTS_CACHE)
            log.debug("contacts cache written (%d keys)", len(names))
        except OSError as exc:
            log.debug("contacts cache write failed: %s", exc)
    return names


def who(handle, is_from_me, contacts):
    if is_from_me:
        return "me"
    if not handle:
        return "?"
    key = handle.strip().lower() if "@" in handle else norm_phone(handle)
    return contacts.get(key, handle)


def fmt(date, handle, is_from_me, body, contacts):
    import datetime
    ts = datetime.datetime.fromtimestamp(apple_ts(date)).strftime("%Y-%m-%d %H:%M")
    body = (body or "").replace("\n", " ").replace("\r", " ")
    return f"{ts}  {who(handle, is_from_me, contacts):<22.22}  {body}"


FDA_HINT = ("Cannot read ~/Library/Messages/chat.db — most likely the app running this lacks "
            "macOS Full Disk Access:\n"
            "  System Settings > Privacy & Security > Full Disk Access > add your terminal/host app,\n"
            "  then fully quit and reopen it.\n"
            "(If you have never set up Messages on this Mac, the database may not exist yet.)")


def parse_limit(v, default=40):
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        sys.exit(f"limit must be a whole number, got: {v!r}")


def connect_chatdb():
    # Force a real read so a TCC/Full-Disk-Access denial surfaces HERE with an actionable message.
    # (os.path.exists() is unreliable: when FDA is denied it returns False, masking the real cause.)
    try:
        con = open_readonly(CHATDB, uri=True)
        con.execute("SELECT 1 FROM message LIMIT 1")
        return con
    except sqlite3.OperationalError as exc:
        log.debug("chat.db open/probe failed: %s", exc)
        sys.exit(FDA_HINT)


def main():
    args = sys.argv[1:]
    _setup_logging(_resolve_log_level(args))
    if not args:
        sys.exit(__doc__)
    cmd = args[0]
    log.info("run: %s", cmd)

    if cmd == "contacts":
        name = args[1] if len(args) > 1 else sys.exit("need a name")
        like = f"%{name}%"
        seen = set()
        opened = 0
        # UNION of phones + emails so a contact with BOTH still shows both (a plain
        # COALESCE(phone, email) over the join would silently drop the email).
        q = """
            SELECT TRIM(COALESCE(r.ZFIRSTNAME,'')||' '||COALESCE(r.ZLASTNAME,'')) name, p.ZFULLNUMBER contact
            FROM ZABCDRECORD r JOIN ZABCDPHONENUMBER p ON p.ZOWNER=r.Z_PK
            WHERE (r.ZFIRSTNAME LIKE ? OR r.ZLASTNAME LIKE ?) AND p.ZFULLNUMBER IS NOT NULL
            UNION
            SELECT TRIM(COALESCE(r.ZFIRSTNAME,'')||' '||COALESCE(r.ZLASTNAME,'')) name, e.ZADDRESS contact
            FROM ZABCDRECORD r JOIN ZABCDEMAILADDRESS e ON e.ZOWNER=r.Z_PK
            WHERE (r.ZFIRSTNAME LIKE ? OR r.ZLASTNAME LIKE ?) AND e.ZADDRESS IS NOT NULL"""
        for db in AB_GLOB:
            if not os.path.exists(db):
                continue
            try:
                with closing(open_readonly(f"file:{db}?mode=ro", uri=True)) as c:
                    rows = c.execute(q, (like, like, like, like)).fetchall()
                opened += 1
                for n, contact in rows:
                    if (n, contact) not in seen:
                        seen.add((n, contact))
                        print(f"{n}\t{contact}")
            except sqlite3.Error as exc:
                log.debug("AddressBook source unreadable (%s): %s", db, exc)
                continue
        if opened == 0:                      # every AddressBook source was unreadable -> almost certainly FDA
            log.error("no readable AddressBook source (Full Disk Access?)")
            sys.exit(FDA_HINT)
        log.info("contacts: %d row(s) from %d source(s)", len(seen), opened)
        return

    with closing(connect_chatdb()) as con:
        contacts = load_contacts()
        base = ("SELECT m.date, m.is_from_me, m.text, m.attributedBody, h.id "
                "FROM message m LEFT JOIN handle h ON m.handle_id = h.ROWID "
                "WHERE COALESCE(m.associated_message_type,0) = 0 ")  # exclude tapbacks/reactions

        if cmd == "recent":
            limit = parse_limit(args[1] if len(args) > 1 else None)
            n = 0
            for date, fromme, text, blob, h in con.execute(base + "ORDER BY m.date DESC LIMIT ?", (limit,)):
                print(fmt(date, h, fromme, decode_body(text, blob), contacts))
                n += 1
            log.info("recent: %d message(s)", n)
            return

        if cmd == "handle":
            h = args[1] if len(args) > 1 else sys.exit("need a phone/email")
            limit = parse_limit(args[2] if len(args) > 2 else None)
            n = 0
            for date, fromme, text, blob, hid in con.execute(
                    base + "AND h.id LIKE ? ORDER BY m.date DESC LIMIT ?", (f"%{h}%", limit)):
                print(fmt(date, hid, fromme, decode_body(text, blob), contacts))
                n += 1
            log.info("handle: %d message(s)", n)
            return

        if cmd == "unread":
            rest = [a for a in args[1:] if not a.startswith("--")]
            limit = parse_limit(rest[0] if len(rest) > 0 else None, 25)
            days = parse_limit(rest[1] if len(rest) > 1 else None, 14)
            cutoff = int((time.time() - days * 86400 - 978307200) * 1_000_000_000)   # ns-since-2001 boundary
            n = 0
            for date, fromme, text, blob, h in con.execute(
                    base + "AND m.is_from_me=0 AND m.is_read=0 AND m.date > ? ORDER BY m.date DESC LIMIT ?",
                    (cutoff, limit)):
                print(fmt(date, h, fromme, decode_body(text, blob), contacts))
                n += 1
            log.info("unread: %d message(s) within %dd", n, days)
            return

        if cmd == "chat":
            ident = args[1] if len(args) > 1 else sys.exit("need a chat identifier (chat_identifier / room name)")
            limit = parse_limit(args[2] if len(args) > 2 else None)
            n = 0
            for date, fromme, text, blob, h in con.execute(
                    "SELECT m.date, m.is_from_me, m.text, m.attributedBody, h.id "
                    "FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
                    "JOIN chat ch ON ch.ROWID = cmj.chat_id "
                    "LEFT JOIN handle h ON m.handle_id = h.ROWID "
                    "WHERE COALESCE(m.associated_message_type,0)=0 "
                    "AND (ch.chat_identifier = ? OR ch.room_name = ? OR ch.guid LIKE ?) "
                    "ORDER BY m.date DESC LIMIT ?",
                    (ident, ident, f"%{ident}%", limit)):
                print(fmt(date, h, fromme, decode_body(text, blob), contacts))
                n += 1
            log.info("chat: %d message(s)", n)
            return

        if cmd == "text":
            term = (args[1] if len(args) > 1 else sys.exit("need a search term")).lower()
            flags = args[2:]
            rest = [a for a in flags if not a.startswith("--")]
            limit = parse_limit(rest[0] if rest else None)
            scan_cap = 10**9 if "--all" in flags else TEXT_SCAN_CAP   # flags only — never mistake the term for --all
            matches = scanned = 0
            for date, fromme, text, blob, h in con.execute(base + "ORDER BY m.date DESC"):
                scanned += 1
                if scanned > scan_cap:
                    break
                body = decode_body(text, blob)
                if body and term in body.lower():
                    if matches >= limit:                      # check before printing so limit 0 yields 0 rows
                        break
                    print(fmt(date, h, fromme, body, contacts))
                    matches += 1
            if scanned >= scan_cap and matches < limit:
                print(f"\n[scanned {scan_cap} newest messages; pass --all to search older history]",
                      file=sys.stderr)
            log.info("text: %d match(es) in %d scanned (cap=%d)", matches, scanned, scan_cap)
            return

    sys.exit(__doc__)


if __name__ == "__main__":
    main()
