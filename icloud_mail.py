#!/usr/bin/env python3
"""
icloud-mail — read / draft / (optionally) send via iCloud IMAP+SMTP.

WHAT: on-demand CLI that talks to Apple's iCloud mail servers directly.
  No AppleScript, no Mail.app, no Envelope Index writes. Credentials live in
  the macOS Keychain (app-specific password) — never in this repo.

PERMISSION MODEL (default: read + draft only):
  ICLOUD_MAIL_PERMS=read,draft          # default
  ICLOUD_MAIL_PERMS=read,draft,send     # unlock send path
  Send ALSO requires an explicit --allow-send flag on the command (two gates).

USAGE:
  icloud-mail setup                     # guided setup: opens Apple ID in browser + macOS dialogs
  icloud-mail auth-set                  # re-store app-specific password (also opens browser)
  icloud-mail auth-check                # probe login (read)
  icloud-mail folders | recent | unread | search | read | drafts | draft | send | send-draft | perms

Env:
  ICLOUD_MAIL_USER     iCloud/Apple ID email (else ~/.config/icloud-mail/config.json)
  ICLOUD_MAIL_PERMS    comma list: read,draft,send  (default: read,draft)
  ICLOUD_MAIL_KEYCHAIN Keychain service name (default: icloud-mail; falls back to icloud-mcp)
  ICLOUD_MAIL_LOG / -v / -vv / -q

Setup (once per Mac / per person):
  icloud-mail setup
  → browser opens appleid.apple.com for sign-in + App-Specific Password
  → macOS dialogs collect iCloud email + that password
  → Keychain + local config written; IMAP verified
"""
from __future__ import annotations

import email
import email.policy
import getpass
import imaplib
import json
import logging
import os
import re
import smtplib
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parsedate_to_datetime
from typing import Iterable

# --- constants ----------------------------------------------------------------
HOME = os.path.expanduser("~")
CONFIG_DIR = os.path.join(HOME, ".config", "icloud-mail")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
# Apple ID account page — user signs in, then generates an App-Specific Password.
APPLE_ID_URL = "https://appleid.apple.com/account/manage"
APPLE_APP_PASSWORD_HELP = "https://support.apple.com/en-us/HT204397"
KEYCHAIN_SERVICE = os.environ.get("ICLOUD_MAIL_KEYCHAIN", "icloud-mail")
KEYCHAIN_FALLBACK_SERVICE = "icloud-mcp"  # prior launcher convention
IMAP_HOST = "imap.mail.me.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.mail.me.com"
SMTP_PORT = 587
DEFAULT_PERMS = frozenset({"read", "draft"})
ALL_PERMS = frozenset({"read", "draft", "send"})
DRAFTS_CANDIDATES = ("Drafts", "INBOX.Drafts", "Draft")
SENT_CANDIDATES = ("Sent Messages", "Sent", "INBOX.Sent")

log = logging.getLogger("icloud_mail")
_LOG_FLAGS = {
    "-q": logging.ERROR, "--quiet": logging.ERROR,
    "-v": logging.INFO, "--verbose": logging.INFO,
    "-vv": logging.DEBUG, "--debug": logging.DEBUG,
}


# --- logging / CLI helpers ----------------------------------------------------
def _resolve_log_level(args: list[str]) -> int:
    level = logging.WARNING
    for tok in list(args):
        if tok in _LOG_FLAGS:
            level = _LOG_FLAGS[tok]
            args.remove(tok)
    env = os.environ.get("ICLOUD_MAIL_LOG") or os.environ.get("MAIL_SEARCH_LOG")
    if env:
        level = getattr(logging, env.strip().upper(), level)
    return level


def _setup_logging(level: int) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    log.handlers[:] = [handler]
    log.setLevel(level)


def parse_limit(v, default=40) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        sys.exit(f"limit must be a whole number, got: {v!r}")


def parse_perms(raw: str | None = None) -> frozenset[str]:
    text = (raw if raw is not None else os.environ.get("ICLOUD_MAIL_PERMS", "read,draft")).strip()
    if not text:
        return DEFAULT_PERMS
    parts = {p.strip().lower() for p in text.split(",") if p.strip()}
    bad = parts - ALL_PERMS
    if bad:
        sys.exit(f"unknown ICLOUD_MAIL_PERMS: {sorted(bad)}; allowed: {sorted(ALL_PERMS)}")
    if not parts:
        return DEFAULT_PERMS
    return frozenset(parts)


def require_perm(need: str, perms: frozenset[str] | None = None) -> None:
    active = perms if perms is not None else parse_perms()
    if need not in active:
        sys.exit(
            f"permission denied: need '{need}' (active: {','.join(sorted(active)) or '(none)'}).\n"
            f"  Default is read,draft only. To enable send:\n"
            f"    export ICLOUD_MAIL_PERMS=read,draft,send\n"
            f"  and pass --allow-send on the send command."
        )


def require_send_gates(args: list[str], perms: frozenset[str] | None = None) -> None:
    """Two gates: perm set must include send, AND --allow-send on the CLI."""
    require_perm("send", perms)
    if "--allow-send" not in args:
        sys.exit(
            "refusing to send: pass --allow-send (second gate).\n"
            "  Example: icloud-mail send --to a@b.com --subject Hi --body '...' --allow-send"
        )


def pop_flag(args: list[str], flag: str) -> bool:
    if flag in args:
        args.remove(flag)
        return True
    return False


def pop_option(args: list[str], *names: str) -> str | None:
    """Pop --name VALUE (or --name=VALUE)."""
    for i, tok in enumerate(list(args)):
        for name in names:
            if tok == name and i + 1 < len(args):
                val = args[i + 1]
                del args[i:i + 2]
                return val
            if tok.startswith(name + "="):
                val = tok.split("=", 1)[1]
                args.pop(i)
                return val
    return None


# --- user config --------------------------------------------------------------
def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def save_config(data: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)
    log.debug("wrote config %s", CONFIG_PATH)


def get_user(explicit: str | None = None) -> str:
    """Resolve iCloud login email: CLI --user > env > config file."""
    if explicit and explicit.strip():
        return explicit.strip()
    env = (os.environ.get("ICLOUD_MAIL_USER") or "").strip()
    if env:
        return env
    user = (load_config().get("user") or "").strip()
    if user:
        return user
    sys.exit(
        "no iCloud user configured yet.\n"
        "  Run:  icloud-mail setup\n"
        "  (or set ICLOUD_MAIL_USER=you@icloud.com and icloud-mail auth-set)"
    )


def open_apple_id_browser() -> None:
    """Open Apple ID in the default browser so the user can sign in + make an app password."""
    print("Opening Apple ID in your browser…", file=sys.stderr)
    print("  1) Sign in with your Apple ID", file=sys.stderr)
    print("  2) Sign-In and Security → App-Specific Passwords → Generate", file=sys.stderr)
    print(f"  Help: {APPLE_APP_PASSWORD_HELP}", file=sys.stderr)
    try:
        # Prefer macOS `open` so it always hits the default browser on this Mac.
        r = subprocess.run(["open", APPLE_ID_URL], check=False, capture_output=True)
        if r.returncode != 0:
            webbrowser.open(APPLE_ID_URL)
    except OSError:
        webbrowser.open(APPLE_ID_URL)


def _osascript_dialog(prompt: str, *, hidden: bool = False, default: str = "") -> str | None:
    """macOS GUI dialog; returns text or None if cancelled / unavailable."""
    # Escape for AppleScript string literals.
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    hidden_clause = " with hidden answer" if hidden else ""
    default_clause = f' default answer "{esc(default)}"' if default is not None else ' default answer ""'
    script = (
        f'display dialog "{esc(prompt)}"{default_clause}{hidden_clause} '
        f'buttons {{"Cancel", "OK"}} default button "OK" '
        f'with title "icloud-mail setup"'
    )
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            check=False, capture_output=True, text=True,
        )
    except OSError as exc:
        log.debug("osascript unavailable: %s", exc)
        return None
    if r.returncode != 0:
        return None
    # stdout like: button returned:OK, text returned:VALUE
    text = r.stdout or ""
    m = re.search(r"text returned:(.*)$", text, re.S)
    if not m:
        return ""
    return m.group(1).rstrip("\n")


def prompt_text(prompt: str, *, hidden: bool = False, default: str = "") -> str:
    """Prefer a macOS dialog (works when an agent launches setup); fall back to TTY."""
    gui = _osascript_dialog(prompt, hidden=hidden, default=default)
    if gui is not None:
        return gui.strip()
    # TTY fallback
    if hidden:
        return getpass.getpass(f"{prompt}: ").strip()
    try:
        return input(f"{prompt} [{default}]: " if default else f"{prompt}: ").strip() or default
    except EOFError:
        sys.exit("no input available — re-run in Terminal, or use a GUI session")


# --- Keychain -----------------------------------------------------------------
def keychain_get(user: str, service: str = KEYCHAIN_SERVICE) -> str | None:
    """Return password from macOS Keychain, or None if missing."""
    for svc in (service, KEYCHAIN_FALLBACK_SERVICE):
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-a", user, "-s", svc, "-w"],
                check=False, capture_output=True, text=True,
            )
        except OSError as exc:  # pragma: no cover - no security binary
            log.debug("keychain exec failed: %s", exc)
            return None
        if out.returncode == 0:
            pw = (out.stdout or "").strip()
            if pw:
                log.debug("keychain hit service=%s", svc)
                return pw
    return None


def keychain_set(password: str, user: str, service: str = KEYCHAIN_SERVICE) -> None:
    """Store/replace app-specific password in login Keychain."""
    subprocess.run(
        ["security", "delete-generic-password", "-a", user, "-s", service],
        check=False, capture_output=True,
    )
    r = subprocess.run(
        ["security", "add-generic-password", "-a", user, "-s", service,
         "-w", password, "-l", f"icloud-mail ({user})"],
        check=False, capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit(f"keychain store failed: {(r.stderr or r.stdout or '').strip()}")
    log.info("stored credentials for %s (service=%s)", user, service)


def require_password(user: str | None = None) -> str:
    user = user or get_user()
    pw = keychain_get(user)
    if not pw:
        sys.exit(
            "no iCloud mail password in Keychain.\n"
            "  Run:  icloud-mail setup\n"
            f"  (looking for account={user!r} service={KEYCHAIN_SERVICE!r})"
        )
    return pw


# --- IMAP / SMTP --------------------------------------------------------------
def imap_connect(user: str, password: str, imap_factory=imaplib.IMAP4_SSL):
    con = imap_factory(IMAP_HOST, IMAP_PORT)
    typ, _ = con.login(user, password)
    if typ != "OK":
        con.logout()
        sys.exit("IMAP login failed")
    return con


def smtp_send(user: str, password: str, msg: EmailMessage, smtp_factory=smtplib.SMTP) -> None:
    with smtp_factory(SMTP_HOST, SMTP_PORT, timeout=60) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(msg)


def list_folders(con) -> list[str]:
    typ, data = con.list()
    if typ != "OK" or not data:
        return []
    out = []
    for raw in data:
        if not raw:
            continue
        line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        # ... "INBOX"  or ... Drafts
        m = re.search(r' "([^"]+)"$| ([^\s]+)$', line)
        if m:
            out.append(m.group(1) or m.group(2))
    return out


def resolve_folder(con, candidates: Iterable[str], fallback: str) -> str:
    folders = list_folders(con)
    lower = {f.lower(): f for f in folders}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
        # path suffix match
        for f in folders:
            if f.lower().endswith(c.lower()):
                return f
    return fallback


def select_folder(con, folder: str, readonly: bool = True):
    typ, data = con.select(folder, readonly=readonly)
    if typ != "OK":
        sys.exit(f"cannot select folder {folder!r}: {data}")


def _decode_header(val: str | None) -> str:
    if not val:
        return ""
    try:
        parts = email.header.decode_header(val)
    except Exception:
        return val
    out = []
    for frag, enc in parts:
        if isinstance(frag, bytes):
            out.append(frag.decode(enc or "utf-8", "replace"))
        else:
            out.append(frag)
    return "".join(out)


def msg_summary_line(uid: str, msg: email.message.Message) -> str:
    date_raw = msg.get("Date")
    try:
        dt = parsedate_to_datetime(date_raw) if date_raw else None
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts = dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else "????-??-?? ??:??"
    except (TypeError, ValueError, OverflowError, OSError):
        ts = "????-??-?? ??:??"
    frm = _decode_header(msg.get("From"))
    subj = _decode_header(msg.get("Subject")) or "(no subject)"
    flags = ""
    return f"{ts}  uid={uid:<8}  {frm:<36.36}  {subj}"


def fetch_uid_headers(con, uid: str) -> email.message.Message | None:
    typ, data = con.uid("fetch", uid, "(BODY.PEEK[HEADER])")
    if typ != "OK" or not data or data[0] is None:
        return None
    # data[0] is (meta, bytes) or similar
    raw = None
    for part in data:
        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
            raw = part[1]
            break
    if raw is None:
        return None
    return email.message_from_bytes(raw, policy=email.policy.default)


def fetch_uid_full(con, uid: str) -> email.message.Message | None:
    typ, data = con.uid("fetch", uid, "(RFC822)")
    if typ != "OK" or not data or data[0] is None:
        return None
    raw = None
    for part in data:
        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
            raw = part[1]
            break
    if raw is None:
        return None
    return email.message_from_bytes(raw, policy=email.policy.default)


def body_text(msg: email.message.Message, max_chars: int = 8000) -> str:
    """Prefer text/plain; fall back to stripped text/html."""
    if msg.is_multipart():
        plain = html = None
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp:
                continue
            try:
                payload = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    payload = payload.decode(part.get_content_charset() or "utf-8", "replace")
            if ctype == "text/plain" and plain is None:
                plain = str(payload)
            elif ctype == "text/html" and html is None:
                html = str(payload)
        text = plain if plain is not None else (re.sub(r"<[^>]+>", " ", html or ""))
    else:
        try:
            text = str(msg.get_content())
        except Exception:
            raw = msg.get_payload(decode=True)
            text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(msg.get_payload())
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        return text[:max_chars] + f"\n…[truncated {len(text) - max_chars} chars]"
    return text


def search_uids(con, *criteria: str) -> list[str]:
    typ, data = con.uid("search", None, *criteria)
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].decode().split()


def list_recent(con, limit: int, unseen_only: bool = False) -> list[tuple[str, email.message.Message]]:
    crit = ("UNSEEN",) if unseen_only else ("ALL",)
    uids = search_uids(con, *crit)
    # newest last in IMAP often; take tail
    uids = uids[-limit:]
    uids.reverse()  # newest first for display
    out = []
    for uid in uids:
        msg = fetch_uid_headers(con, uid)
        if msg is not None:
            out.append((uid, msg))
    return out


def build_message(
    *,
    user: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    from_name: str | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, user)) if from_name else user
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="icloud.com")
    msg.set_content(body)
    return msg


def split_addrs(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [a.strip() for a in re.split(r"[,;]", raw) if a.strip()]


def append_draft(con, msg: EmailMessage, folder: str | None = None) -> str:
    folder = folder or resolve_folder(con, DRAFTS_CANDIDATES, "Drafts")
    raw = msg.as_bytes(policy=email.policy.SMTP)
    typ, data = con.append(folder, "\\Draft", None, raw)
    if typ != "OK":
        sys.exit(f"APPEND draft failed: {data}")
    # IMAP APPENDUID response: [b'APPENDUID 123 456'] optional
    log.info("draft appended to %s", folder)
    return folder


# --- commands -----------------------------------------------------------------
def cmd_setup(args: list[str]) -> None:
    """Guided first-run: browser → Apple ID, GUI dialogs → Keychain, then verify IMAP."""
    no_browser = pop_flag(args, "--no-browser")
    user = pop_option(args, "--user")
    print("", file=sys.stderr)
    print("=== icloud-mail setup ===", file=sys.stderr)
    print("This never uses your real Apple ID password.", file=sys.stderr)
    print("You will create a one-time App-Specific Password on Apple's site.", file=sys.stderr)
    print("", file=sys.stderr)

    if not user:
        default_user = (os.environ.get("ICLOUD_MAIL_USER") or load_config().get("user") or "")
        user = prompt_text(
            "Your iCloud email (Apple ID), e.g. name@icloud.com",
            default=str(default_user or ""),
        )
    user = (user or "").strip()
    if not user or "@" not in user:
        sys.exit("need a valid email address (use --user you@icloud.com)")

    if not no_browser:
        open_apple_id_browser()
        print("", file=sys.stderr)
        print("In the browser: generate an App-Specific Password (label it e.g. “icloud-mail”).",
              file=sys.stderr)
        print("Then paste it into the dialog that appears.", file=sys.stderr)
        print("", file=sys.stderr)

    pw = prompt_text(
        "Paste the App-Specific Password from Apple (xxxx-xxxx-xxxx-xxxx)",
        hidden=True,
    )
    if not pw:
        sys.exit("empty password — nothing stored. Re-run: icloud-mail setup")

    # Normalize: Apple shows passwords with dashes; IMAP accepts with or without.
    pw_norm = pw.replace(" ", "")
    keychain_set(pw_norm, user=user)
    cfg = load_config()
    cfg["user"] = user
    save_config(cfg)
    print(f"Saved account {user} to Keychain + {CONFIG_PATH}", file=sys.stderr)

    # Verify immediately
    print("Checking IMAP login…", file=sys.stderr)
    try:
        con = imap_connect(user, pw_norm)
        try:
            n = len(list_folders(con))
            print(f"OK — signed in as {user} ({n} mailbox(es)). Setup complete.", file=sys.stderr)
        finally:
            try:
                con.logout()
            except Exception:
                pass
    except SystemExit:
        print("Login failed. Delete the bad Keychain item and re-run setup:", file=sys.stderr)
        print(f"  security delete-generic-password -a {user!r} -s {KEYCHAIN_SERVICE}", file=sys.stderr)
        raise
    print("", file=sys.stderr)
    print("Try:  icloud-mail unread 5", file=sys.stderr)
    print("Send stays OFF until you set ICLOUD_MAIL_PERMS=read,draft,send and pass --allow-send.",
          file=sys.stderr)


def cmd_auth_set(args: list[str]) -> None:
    """Re-store password (opens browser + dialogs). Prefer `setup` for first run."""
    no_browser = pop_flag(args, "--no-browser")
    user = pop_option(args, "--user")
    if not user:
        try:
            user = get_user()
        except SystemExit:
            user = prompt_text("Your iCloud email (Apple ID)")
    if not no_browser:
        open_apple_id_browser()
    print(f"Storing app-specific password for {user}", file=sys.stderr)
    pw = prompt_text("Paste the App-Specific Password", hidden=True)
    if not pw:
        sys.exit("empty password — nothing stored")
    pw = pw.replace(" ", "")
    keychain_set(pw, user=user)
    cfg = load_config()
    cfg["user"] = user
    save_config(cfg)
    print("OK — stored in Keychain. Run: icloud-mail auth-check", file=sys.stderr)


def cmd_auth_check(args: list[str]) -> None:
    require_perm("read")
    user = pop_option(args, "--user") or get_user()
    pw = require_password(user)
    con = imap_connect(user, pw)
    try:
        folders = list_folders(con)
        print(f"OK — IMAP login as {user}; {len(folders)} mailbox(es)")
        for f in folders[:20]:
            print(f"  {f}")
        if len(folders) > 20:
            print(f"  … +{len(folders) - 20} more")
    finally:
        try:
            con.logout()
        except Exception:
            pass


def cmd_perms(args: list[str]) -> None:
    active = parse_perms()
    print("active:", ",".join(sorted(active)))
    print("default:", ",".join(sorted(DEFAULT_PERMS)))
    print("all:", ",".join(sorted(ALL_PERMS)))
    print("send requires: ICLOUD_MAIL_PERMS includes 'send' AND --allow-send on the command")


def cmd_folders(args: list[str]) -> None:
    require_perm("read")
    user = get_user()
    pw = require_password(user)
    con = imap_connect(user, pw)
    try:
        for f in list_folders(con):
            print(f)
        log.info("folders: listed")
    finally:
        con.logout()


def _with_inbox(args, unseen=False):
    require_perm("read")
    limit = parse_limit(args[0] if args else None)
    folder = pop_option(args, "--folder") or "INBOX"
    user = get_user()
    pw = require_password(user)
    con = imap_connect(user, pw)
    try:
        select_folder(con, folder, readonly=True)
        rows = list_recent(con, limit, unseen_only=unseen)
        for uid, msg in rows:
            print(msg_summary_line(uid, msg))
        log.info("%s: %d message(s) in %s", "unread" if unseen else "recent", len(rows), folder)
    finally:
        con.logout()


def cmd_recent(args: list[str]) -> None:
    _with_inbox(args, unseen=False)


def cmd_unread(args: list[str]) -> None:
    _with_inbox(args, unseen=True)


def cmd_search(args: list[str]) -> None:
    require_perm("read")
    if not args:
        sys.exit("need a search string")
    term = args[0]
    limit = parse_limit(args[1] if len(args) > 1 else None)
    folder = pop_option(args, "--folder") or "INBOX"
    user = get_user()
    pw = require_password(user)
    con = imap_connect(user, pw)
    try:
        select_folder(con, folder, readonly=True)
        # IMAP TEXT is server-side full-text over body+headers
        uids = search_uids(con, "TEXT", term)
        uids = uids[-limit:]
        uids.reverse()
        n = 0
        for uid in uids:
            msg = fetch_uid_headers(con, uid)
            if msg:
                print(msg_summary_line(uid, msg))
                n += 1
        log.info("search: %d hit(s)", n)
    finally:
        con.logout()


def cmd_read(args: list[str]) -> None:
    require_perm("read")
    if not args:
        sys.exit("need a uid")
    uid = args[0]
    folder = pop_option(args, "--folder") or "INBOX"
    user = get_user()
    pw = require_password(user)
    con = imap_connect(user, pw)
    try:
        select_folder(con, folder, readonly=True)
        msg = fetch_uid_full(con, uid)
        if msg is None:
            sys.exit(f"uid {uid} not found in {folder}")
        print(f"From: {_decode_header(msg.get('From'))}")
        print(f"To: {_decode_header(msg.get('To'))}")
        if msg.get("Cc"):
            print(f"Cc: {_decode_header(msg.get('Cc'))}")
        print(f"Date: {msg.get('Date')}")
        print(f"Subject: {_decode_header(msg.get('Subject'))}")
        print(f"Message-ID: {msg.get('Message-ID')}")
        print("---")
        print(body_text(msg))
        log.info("read: uid=%s folder=%s", uid, folder)
    finally:
        con.logout()


def cmd_drafts(args: list[str]) -> None:
    require_perm("read")
    limit = parse_limit(args[0] if args else None)
    user = get_user()
    pw = require_password(user)
    con = imap_connect(user, pw)
    try:
        folder = resolve_folder(con, DRAFTS_CANDIDATES, "Drafts")
        select_folder(con, folder, readonly=True)
        rows = list_recent(con, limit, unseen_only=False)
        for uid, msg in rows:
            print(msg_summary_line(uid, msg))
        log.info("drafts: %d in %s", len(rows), folder)
    finally:
        con.logout()


def cmd_draft(args: list[str]) -> None:
    require_perm("draft")
    to = split_addrs(pop_option(args, "--to"))
    subject = pop_option(args, "--subject", "-s")
    body = pop_option(args, "--body", "-b")
    cc = split_addrs(pop_option(args, "--cc"))
    bcc = split_addrs(pop_option(args, "--bcc"))
    body_file = pop_option(args, "--body-file")
    if body_file:
        with open(body_file, encoding="utf-8") as f:
            body = f.read()
    if not to:
        sys.exit("draft requires --to")
    if subject is None:
        sys.exit("draft requires --subject")
    if body is None:
        sys.exit("draft requires --body or --body-file")
    user = get_user()
    pw = require_password(user)
    msg = build_message(user=user, to=to, subject=subject, body=body, cc=cc or None, bcc=bcc or None)
    con = imap_connect(user, pw)
    try:
        folder = append_draft(con, msg)
        print(f"OK — draft saved to {folder}")
        print(f"  To: {', '.join(to)}")
        print(f"  Subject: {subject}")
    finally:
        con.logout()


def cmd_send(args: list[str]) -> None:
    require_send_gates(args)
    pop_flag(args, "--allow-send")
    to = split_addrs(pop_option(args, "--to"))
    subject = pop_option(args, "--subject", "-s")
    body = pop_option(args, "--body", "-b")
    cc = split_addrs(pop_option(args, "--cc"))
    bcc = split_addrs(pop_option(args, "--bcc"))
    body_file = pop_option(args, "--body-file")
    if body_file:
        with open(body_file, encoding="utf-8") as f:
            body = f.read()
    if not to:
        sys.exit("send requires --to")
    if subject is None:
        sys.exit("send requires --subject")
    if body is None:
        sys.exit("send requires --body or --body-file")
    user = get_user()
    pw = require_password(user)
    msg = build_message(user=user, to=to, subject=subject, body=body, cc=cc or None, bcc=bcc or None)
    smtp_send(user, pw, msg)
    # Best-effort copy to Sent
    try:
        con = imap_connect(user, pw)
        try:
            sent = resolve_folder(con, SENT_CANDIDATES, "Sent Messages")
            con.append(sent, "\\Seen", None, msg.as_bytes(policy=email.policy.SMTP))
            log.info("copied to %s", sent)
        finally:
            con.logout()
    except Exception as exc:
        log.debug("sent-folder copy skipped: %s", exc)
    print(f"OK — sent to {', '.join(to)}")
    print(f"  Subject: {subject}")


def cmd_send_draft(args: list[str]) -> None:
    require_send_gates(args)
    pop_flag(args, "--allow-send")
    if not args:
        sys.exit("need a draft uid")
    uid = args[0]
    user = get_user()
    pw = require_password(user)
    con = imap_connect(user, pw)
    try:
        folder = resolve_folder(con, DRAFTS_CANDIDATES, "Drafts")
        select_folder(con, folder, readonly=True)
        msg = fetch_uid_full(con, uid)
        if msg is None:
            sys.exit(f"draft uid {uid} not found in {folder}")
        # Rebuild as EmailMessage for smtplib
        em = EmailMessage()
        for h in ("From", "To", "Cc", "Bcc", "Subject", "Message-ID", "Date"):
            if msg.get(h):
                em[h] = msg.get(h)
        if not em.get("From"):
            em["From"] = user
        em.set_content(body_text(msg, max_chars=500_000))
        smtp_send(user, pw, em)
        # delete draft after send
        select_folder(con, folder, readonly=False)
        con.uid("store", uid, "+FLAGS", "(\\Deleted)")
        con.expunge()
        print(f"OK — sent draft uid={uid} and removed from {folder}")
    finally:
        try:
            con.logout()
        except Exception:
            pass


COMMANDS = {
    "setup": cmd_setup,
    "auth-set": cmd_auth_set,
    "auth-check": cmd_auth_check,
    "perms": cmd_perms,
    "folders": cmd_folders,
    "recent": cmd_recent,
    "unread": cmd_unread,
    "search": cmd_search,
    "read": cmd_read,
    "drafts": cmd_drafts,
    "draft": cmd_draft,
    "send": cmd_send,
    "send-draft": cmd_send_draft,
}


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    _setup_logging(_resolve_log_level(args))
    if not args:
        sys.exit(__doc__)
    cmd = args.pop(0)
    log.info("run: %s", cmd)
    handler = COMMANDS.get(cmd)
    if not handler:
        sys.exit(f"unknown command {cmd!r}\n\n{__doc__}")
    handler(args)


if __name__ == "__main__":
    main()
