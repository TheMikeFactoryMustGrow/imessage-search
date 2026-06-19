#!/usr/bin/env python3
"""
imessage-search — read-only search of local iMessage history (~/Library/Messages/chat.db).

WHAT: a tiny, on-demand CLI that reads your Messages database directly (read-only) so an
AI agent or you can search your iMessage/SMS history. No daemon, no background process,
NO send capability. macOS only.

WHY IT EXISTS: AppleScript-based iMessage tools are fragile (TCC Automation grants break on
Homebrew node upgrades, the Contacts app must be running, etc.). This bypasses AppleEvents
entirely with direct SQLite reads — robust and fast.

CORRECTNESS: since macOS Ventura, most message text lives in the `attributedBody` BLOB (a NeXT
`typedstream` archive), not the `text` column. A naive `SELECT text` silently misses the large
majority of recent messages. This tool decodes the blob with a real typedstream parser
(pytypedstream). It also handles ns-since-2001 dates, filters tapbacks/reactions, and resolves
handles to contact names across all AddressBook sources.

USAGE:
  imessage-search text   "<substring>" [limit] [--all]   # search message bodies (recent-first)
  imessage-search handle "<phone/email>" [limit]         # all messages with one handle
  imessage-search recent [limit]                         # most recent messages, all chats
  imessage-search contacts "<name>"                      # name -> phone/email (all sources)
Defaults: limit 40. `text` scans newest-first up to 80000 rows; pass --all to scan everything.

PREREQUISITE: the process running this needs macOS Full Disk Access (System Settings >
Privacy & Security > Full Disk Access). See README / AGENTS.md.
"""
import sqlite3, os, sys, glob, re

HOME = os.path.expanduser("~")
CHATDB = f"file:{HOME}/Library/Messages/chat.db?mode=ro"   # ro: WAL-safe, reflects in-flight messages
AB_GLOB = [f"{HOME}/Library/Application Support/AddressBook/AddressBook-v22.abcddb",
           *glob.glob(f"{HOME}/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb")]
OBJ_REPLACEMENT = "￼"  # attachment placeholder char

try:
    import typedstream
except ImportError:
    sys.exit("typedstream parser missing — re-run install.sh (it builds the venv with pytypedstream).")


def decode_body(text, blob):
    """Return the human-readable body: prefer the plain text column, else decode attributedBody."""
    if text:
        return text.replace(OBJ_REPLACEMENT, "").strip() or "[attachment]"
    if not blob:
        return None
    try:
        obj = typedstream.unarchive_from_data(blob)
    except Exception:
        return None
    found = []
    def walk(x, depth=0):
        if depth > 6:
            return
        if isinstance(x, str):
            found.append(x)
        elif isinstance(x, (list, tuple)):
            for i in x:
                walk(i, depth + 1)
        else:
            for attr in ("value", "values", "contents"):
                if hasattr(x, attr):
                    walk(getattr(x, attr), depth + 1)
    walk(obj)
    for s in found:                      # NSAttributedString stores the body as the first string
        s = s.replace(OBJ_REPLACEMENT, "").strip()
        if s:
            return s
    return "[attachment]" if found or blob else None


def apple_ts(date):
    """message.date is ns-since-2001 on modern macOS, seconds on very old; detect by magnitude."""
    secs = date / 1_000_000_000 if date > 1_000_000_000_000 else date
    return secs + 978307200  # 2001-01-01 -> Unix epoch


def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 10 else d


def load_contacts():
    """Map normalized phone (last 10 digits) and lowercased email -> contact name, across all sources."""
    names = {}
    for db in AB_GLOB:
        if not os.path.exists(db):
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
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
            con.close()
        except sqlite3.Error:
            continue
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
    return f"{ts}  {who(handle, is_from_me, contacts):<22.22}  {(body or '').replace(chr(10),' ')}"


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
        con = sqlite3.connect(CHATDB, uri=True)
        con.execute("SELECT 1 FROM message LIMIT 1")
        return con
    except sqlite3.OperationalError:
        sys.exit(FDA_HINT)


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd = args[0]

    if cmd == "contacts":
        name = args[1] if len(args) > 1 else sys.exit("need a name")
        like = f"%{name}%"
        seen = set(); opened = 0
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
                c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                rows = c.execute(q, (like, like, like, like)).fetchall()
                opened += 1
                for n, contact in rows:
                    if (n, contact) not in seen:
                        seen.add((n, contact)); print(f"{n}\t{contact}")
                c.close()
            except sqlite3.Error:
                continue
        if opened == 0:                      # every AddressBook source was unreadable -> almost certainly FDA
            sys.exit(FDA_HINT)
        return

    con = connect_chatdb()
    contacts = load_contacts()
    base = ("SELECT m.date, m.is_from_me, m.text, m.attributedBody, h.id "
            "FROM message m LEFT JOIN handle h ON m.handle_id = h.ROWID "
            "WHERE COALESCE(m.associated_message_type,0) = 0 ")  # exclude tapbacks/reactions

    if cmd == "recent":
        limit = parse_limit(args[1] if len(args) > 1 else None)
        for date, fromme, text, blob, h in con.execute(base + "ORDER BY m.date DESC LIMIT ?", (limit,)):
            print(fmt(date, h, fromme, decode_body(text, blob), contacts))
        return

    if cmd == "handle":
        h = args[1] if len(args) > 1 else sys.exit("need a phone/email")
        limit = parse_limit(args[2] if len(args) > 2 else None)
        for date, fromme, text, blob, hid in con.execute(
                base + "AND h.id LIKE ? ORDER BY m.date DESC LIMIT ?", (f"%{h}%", limit)):
            print(fmt(date, hid, fromme, decode_body(text, blob), contacts))
        return

    if cmd == "text":
        term = (args[1] if len(args) > 1 else sys.exit("need a search term")).lower()
        flags = args[2:]
        rest = [a for a in flags if not a.startswith("--")]
        limit = parse_limit(rest[0] if rest else None)
        scan_cap = 10**9 if "--all" in flags else 80000   # flags only — never mistake the term for --all
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
        return

    sys.exit(__doc__)


if __name__ == "__main__":
    main()
