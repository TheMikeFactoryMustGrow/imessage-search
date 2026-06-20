"""Hermetic tests for imessage_search — no real Messages data, no network, no Full Disk Access.

The attributedBody fixtures are genuine `streamtyped` blobs of SYNTHETIC text, generated once via
macOS NSArchiver and embedded as base64 so the suite has no PyObjC dependency and contains no
private message content. chat.db / AddressBook are built fresh per test in tmp_path.
"""
import base64
import re
import sqlite3
import sys
import time

import pytest

import imessage_search as m

# --- real streamtyped blobs (synthetic text), generated via macOS NSArchiver ------------------
_B64 = {
    "SHORT": "BAtzdHJlYW10eXBlZIHoA4QBQISEhBJOU0F0dHJpYnV0ZWRTdHJpbmcAhIQITlNPYmplY3QAhZKEhIQITlNTdHJpbmcBlIQBKxNIZWxsbyBmcm9tIHRoZSBibG9ihoQCaUkBE5KEhIQMTlNEaWN0aW9uYXJ5AJSEAWkAhoY=",
    "LONG": "BAtzdHJlYW10eXBlZIHoA4QBQISEhBJOU0F0dHJpYnV0ZWRTdHJpbmcAhIQITlNPYmplY3QAhZKEhIQITlNTdHJpbmcBlIQBK4HIAHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4hoQCaUkBgcgAkoSEhAxOU0RpY3Rpb25hcnkAlIQBaQCGhg==",
    "UNICODE": "BAtzdHJlYW10eXBlZIHoA4QBQISEhBJOU0F0dHJpYnV0ZWRTdHJpbmcAhIQITlNPYmplY3QAhZKEhIQITlNTdHJpbmcBlIQBKw5jYWbDqSDimJUgdGVzdIaEAmlJAQuShISEDE5TRGljdGlvbmFyeQCUhAFpAIaG",
    "ATTACH": "BAtzdHJlYW10eXBlZIHoA4QBQISEhBJOU0F0dHJpYnV0ZWRTdHJpbmcAhIQITlNPYmplY3QAhZKEhIQITlNTdHJpbmcBlIQBKwPvv7yGhAJpSQEBkoSEhAxOU0RpY3Rpb25hcnkAlIQBaQCGhg==",
}
def blob(key):
    return base64.b64decode(_B64[key])

APPLE_EPOCH = 978307200
def ns_ago(days):
    """An ns-since-2001 timestamp `days` in the past (matches `unread`'s cutoff math)."""
    return int((time.time() - days * 86400 - APPLE_EPOCH) * 1_000_000_000)


# --- fixtures ---------------------------------------------------------------------------------
def build_chatdb(path):
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);"
        "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date INTEGER, is_from_me INTEGER,"
        " is_read INTEGER, text TEXT, attributedBody BLOB, handle_id INTEGER,"
        " associated_message_type INTEGER);"
        "CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, room_name TEXT, guid TEXT);"
        "CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);"
    )
    con.executemany("INSERT INTO handle (ROWID,id) VALUES (?,?)",
                    [(1, "+13179995955"), (2, "friend@example.com")])
    # ROWID, date, from_me, is_read, text, blob, handle, amt
    rows = [
        (1, ns_ago(0), 1, 1, "hello from me", None, 1, 0),            # from me; matches "hello"
        (2, ns_ago(1), 0, 0, None, blob("SHORT"), 1, 0),             # unread; blob -> "Hello from the blob"
        (3, ns_ago(2), 0, 0, None, blob("ATTACH"), 2, 0),           # unread; attachment-only
        (4, ns_ago(3), 0, 1, "goodbye", None, 2, 0),                # read; no "hello"
        (5, ns_ago(4), 0, 0, None, None, 1, 0),                     # unread; decode -> None (empty body)
        (6, ns_ago(5), 0, 0, "tapback love", None, 1, 2000),       # reaction -> excluded by base filter
        (7, ns_ago(400), 0, 0, "old unread hello", None, 1, 0),    # too old for `unread` cutoff
    ]
    con.executemany(
        "INSERT INTO message (ROWID,date,is_from_me,is_read,text,attributedBody,handle_id,"
        "associated_message_type) VALUES (?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO chat (ROWID,chat_identifier,room_name,guid) VALUES "
                "(1,'chat999','chat999','iMessage;+;chat999')")
    con.execute("INSERT INTO chat_message_join (chat_id,message_id) VALUES (1,1)")
    con.commit(); con.close()


def build_addressbook(path):
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT);"
        "CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER TEXT);"
        "CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT);"
    )
    con.executemany("INSERT INTO ZABCDRECORD (Z_PK,ZFIRSTNAME,ZLASTNAME,ZORGANIZATION) VALUES (?,?,?,?)", [
        (1, "Lindsay", "Lingle", None),       # phone + email
        (2, "Bob", "Phoneonly", None),        # phone, no email  -> `if email` False
        (3, "Eve", "Emailonly", None),        # email, no phone  -> `if phone` False
        (4, None, None, None),                # empty name       -> `if not name` True (skip)
    ])
    con.executemany("INSERT INTO ZABCDPHONENUMBER (Z_PK,ZOWNER,ZFULLNUMBER) VALUES (?,?,?)", [
        (1, 1, "+1 (317) 999-5955"), (2, 2, "+15551112222"), (3, 4, "+15559999999")])
    con.executemany("INSERT INTO ZABCDEMAILADDRESS (Z_PK,ZOWNER,ZADDRESS) VALUES (?,?,?)", [
        (1, 1, "lindsay@example.com"), (2, 3, "eve@example.com")])
    con.commit(); con.close()


@pytest.fixture
def chatdb(tmp_path, monkeypatch):
    p = tmp_path / "chat.db"
    build_chatdb(str(p))
    monkeypatch.setattr(m, "CHATDB", f"file:{p}?mode=ro")
    return p


@pytest.fixture
def ab(tmp_path, monkeypatch):
    p = tmp_path / "AddressBook-v22.abcddb"
    build_addressbook(str(p))
    monkeypatch.setattr(m, "AB_GLOB", [str(p)])
    return p


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["imessage-search", *argv])
    m.main()


# --- _collect_strings ------------------------------------------------------------------------
def test_collect_strings_str():
    assert m._collect_strings("x") == ["x"]

def test_collect_strings_list_and_tuple():
    assert m._collect_strings(["a", ["b"]]) == ["a", "b"]
    assert m._collect_strings(("a",)) == ["a"]

def test_collect_strings_depth_guard():
    nested = "deep"
    for _ in range(9):
        nested = [nested]
    assert m._collect_strings(nested) == []        # recursion guard trips before reaching the string

def test_collect_strings_object_attr():
    class HasValue:
        value = "v"
    assert m._collect_strings(HasValue()) == ["v"]

def test_collect_strings_object_without_attrs():
    assert m._collect_strings(5) == []             # not str/list and no value/values/contents


# --- decode_body -----------------------------------------------------------------------------
def test_decode_body_text_strips_obj_char():
    assert m.decode_body("hi￼ there", None) == "hi there"

def test_decode_body_text_only_obj_char_is_attachment():
    assert m.decode_body("￼", None) == "[attachment]"

def test_decode_body_no_text_no_blob():
    assert m.decode_body(None, None) is None
    assert m.decode_body("", None) is None

def test_decode_body_garbage_blob():
    assert m.decode_body(None, b"\x00\x01\x02\x03") is None

def test_decode_body_blob_short():
    assert m.decode_body(None, blob("SHORT")) == "Hello from the blob"

def test_decode_body_blob_long():
    assert m.decode_body(None, blob("LONG")) == "x" * 200

def test_decode_body_blob_unicode():
    assert m.decode_body(None, blob("UNICODE")) == "café ☕ test"

def test_decode_body_blob_attachment_only():
    assert m.decode_body(None, blob("ATTACH")) == "[attachment]"


# --- apple_ts / norm_phone -------------------------------------------------------------------
def test_apple_ts_nanoseconds():
    assert m.apple_ts(700_000_000_000_000_000) == 700_000_000 + APPLE_EPOCH

def test_apple_ts_seconds_legacy():
    assert m.apple_ts(500_000_000) == 500_000_000 + APPLE_EPOCH

def test_norm_phone_full():
    assert m.norm_phone("+1 (317) 999-5955") == "3179995955"

def test_norm_phone_short_and_empty():
    assert m.norm_phone("123") == "123"
    assert m.norm_phone(None) == ""


# --- who / fmt -------------------------------------------------------------------------------
def test_who_from_me():
    assert m.who("+1555", 1, {}) == "me"

def test_who_no_handle():
    assert m.who("", 0, {}) == "?"

def test_who_email_resolved_and_unresolved():
    assert m.who("X@Y.com", 0, {"x@y.com": "Bob"}) == "Bob"
    assert m.who("z@y.com", 0, {}) == "z@y.com"

def test_who_phone_resolved():
    assert m.who("+13179995955", 0, {"3179995955": "Lindsay"}) == "Lindsay"

def test_fmt_shape_and_newline_flattened():
    out = m.fmt(ns_ago(0), "z@y.com", 0, "line1\nline2", {})
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}  z@y\.com\s+line1 line2$", out)

def test_fmt_none_body():
    out = m.fmt(ns_ago(0), "z@y.com", 0, None, {})
    assert out.endswith("  ")  # empty body


# --- parse_limit -----------------------------------------------------------------------------
def test_parse_limit_value_default_and_invalid():
    assert m.parse_limit("5") == 5
    assert m.parse_limit(None) == 40
    assert m.parse_limit(None, 25) == 25
    with pytest.raises(SystemExit):
        m.parse_limit("abc")


# --- connect_chatdb --------------------------------------------------------------------------
def test_connect_chatdb_success(chatdb):
    con = m.connect_chatdb()
    assert con.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 7

def test_connect_chatdb_failure(monkeypatch):
    monkeypatch.setattr(m, "CHATDB", "file:/nonexistent/definitely-not-here.db?mode=ro")
    with pytest.raises(SystemExit) as e:
        m.connect_chatdb()
    assert "Full Disk Access" in str(e.value)


# --- load_contacts ---------------------------------------------------------------------------
def test_load_contacts(tmp_path, monkeypatch):
    good = tmp_path / "good.abcddb"; build_addressbook(str(good))
    broken = tmp_path / "broken.abcddb"; broken.write_bytes(b"not a database")
    missing = tmp_path / "missing.abcddb"
    monkeypatch.setattr(m, "AB_GLOB", [str(missing), str(broken), str(good)])
    names = m.load_contacts()
    assert names["3179995955"] == "Lindsay Lingle"
    assert names["lindsay@example.com"] == "Lindsay Lingle"
    assert names["5551112222"] == "Bob Phoneonly"       # phone-only record
    assert names["eve@example.com"] == "Eve Emailonly"  # email-only record
    assert "5559999999" not in names                    # empty-name record skipped


# --- main: argument handling -----------------------------------------------------------------
def test_main_no_args():
    import builtins  # noqa
    with pytest.raises(SystemExit):
        run_main(pytest.MonkeyPatch())  # no argv beyond prog

def test_main_unknown_verb(chatdb, ab, monkeypatch):
    with pytest.raises(SystemExit):
        run_main(monkeypatch, "frobnicate")


# --- main: recent / handle -------------------------------------------------------------------
def test_main_recent(chatdb, ab, monkeypatch, capsys):
    run_main(monkeypatch, "recent", "3")
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 3
    assert any("hello from me" in l for l in out)
    assert any("Hello from the blob" in l for l in out)
    assert any("[attachment]" in l for l in out)
    assert all("tapback" not in l for l in out)   # reaction excluded

def test_main_recent_default_limit(chatdb, ab, monkeypatch, capsys):
    run_main(monkeypatch, "recent")
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 6   # 7 rows minus the 1 tapback

def test_main_handle_resolves_name(chatdb, ab, monkeypatch, capsys):
    run_main(monkeypatch, "handle", "+13179995955", "10")
    out = capsys.readouterr().out
    assert "Lindsay Lingle" in out          # contact name resolved
    assert "old unread hello" in out

def test_main_handle_missing_arg(chatdb, ab, monkeypatch):
    with pytest.raises(SystemExit):
        run_main(monkeypatch, "handle")


# --- main: unread ----------------------------------------------------------------------------
def test_main_unread_default_window(chatdb, ab, monkeypatch, capsys):
    run_main(monkeypatch, "unread")
    out = capsys.readouterr().out
    assert "Hello from the blob" in out      # 1 day old, unread
    assert "old unread hello" not in out     # 400 days old -> outside 14-day cutoff
    assert "hello from me" not in out        # from_me excluded
    assert "goodbye" not in out              # read excluded

def test_main_unread_wide_window(chatdb, ab, monkeypatch, capsys):
    run_main(monkeypatch, "unread", "25", "500")
    assert "old unread hello" in capsys.readouterr().out


# --- main: chat ------------------------------------------------------------------------------
def test_main_chat_by_identifier(chatdb, ab, monkeypatch, capsys):
    run_main(monkeypatch, "chat", "chat999", "10")
    assert "hello from me" in capsys.readouterr().out

def test_main_chat_by_guid_substring(chatdb, ab, monkeypatch, capsys):
    run_main(monkeypatch, "chat", "iMessage;+;chat999")
    assert "hello from me" in capsys.readouterr().out

def test_main_chat_missing_arg(chatdb, ab, monkeypatch):
    with pytest.raises(SystemExit):
        run_main(monkeypatch, "chat")


# --- main: text ------------------------------------------------------------------------------
def test_main_text_matches(chatdb, ab, monkeypatch, capsys):
    run_main(monkeypatch, "text", "hello", "10")
    out = capsys.readouterr().out
    assert "hello from me" in out
    assert "Hello from the blob" in out      # blob body matched
    assert "old unread hello" in out

def test_main_text_no_match(chatdb, ab, monkeypatch, capsys):
    run_main(monkeypatch, "text", "zzzn-no-such-term")
    assert capsys.readouterr().out.strip() == ""

def test_main_text_limit_zero(chatdb, ab, monkeypatch, capsys):
    run_main(monkeypatch, "text", "hello", "0")
    assert capsys.readouterr().out.strip() == ""

def test_main_text_all_flag(chatdb, ab, monkeypatch, capsys):
    run_main(monkeypatch, "text", "hello", "--all")
    assert "hello from me" in capsys.readouterr().out

def test_main_text_missing_term(chatdb, ab, monkeypatch):
    with pytest.raises(SystemExit):
        run_main(monkeypatch, "text")

def test_main_text_scan_cap_message(chatdb, ab, monkeypatch, capsys):
    monkeypatch.setattr(m, "TEXT_SCAN_CAP", 1)       # force the cap path with a tiny db
    run_main(monkeypatch, "text", "zzzn-no-such-term")
    assert "scanned 1 newest messages" in capsys.readouterr().err


# --- main: contacts --------------------------------------------------------------------------
def test_main_contacts_lists_phone_and_email(tmp_path, monkeypatch, capsys):
    good = tmp_path / "g.abcddb"; build_addressbook(str(good))
    monkeypatch.setattr(m, "AB_GLOB", [str(good)])
    run_main(monkeypatch, "contacts", "Lingle")
    out = capsys.readouterr().out
    assert "Lindsay Lingle\t+1 (317) 999-5955" in out
    assert "Lindsay Lingle\tlindsay@example.com" in out

def test_main_contacts_dedup_across_sources(tmp_path, monkeypatch, capsys):
    good = tmp_path / "g.abcddb"; build_addressbook(str(good))
    monkeypatch.setattr(m, "AB_GLOB", [str(good), str(good)])   # same db twice -> dedup branch
    run_main(monkeypatch, "contacts", "Lindsay")
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == len(set(lines))                       # no duplicate rows printed

def test_main_contacts_missing_name(ab, monkeypatch):
    with pytest.raises(SystemExit):
        run_main(monkeypatch, "contacts")

def test_main_contacts_unreadable_sources_hint(tmp_path, monkeypatch):
    broken = tmp_path / "broken.abcddb"; broken.write_bytes(b"not a database")
    missing = tmp_path / "missing.abcddb"
    monkeypatch.setattr(m, "AB_GLOB", [str(missing), str(broken)])  # none open -> FDA hint
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, "contacts", "Lindsay")
    assert "Full Disk Access" in str(e.value)
