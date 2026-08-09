"""Hermetic tests for mail_search — synthetic Envelope Index, no real Mail data."""
import logging
import sqlite3
import sys
import time

import pytest

import mail_search as m


def build_envelope(path):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE messages (
          ROWID INTEGER PRIMARY KEY,
          message_id INTEGER,
          sender INTEGER,
          subject INTEGER,
          summary INTEGER,
          date_received INTEGER,
          mailbox INTEGER,
          read INTEGER,
          deleted INTEGER
        );
        CREATE TABLE subjects (ROWID INTEGER PRIMARY KEY, subject TEXT);
        CREATE TABLE addresses (ROWID INTEGER PRIMARY KEY, address TEXT, comment TEXT);
        CREATE TABLE mailboxes (
          ROWID INTEGER PRIMARY KEY, url TEXT,
          total_count INTEGER, unread_count INTEGER
        );
        """
    )
    con.executemany(
        "INSERT INTO subjects (ROWID, subject) VALUES (?,?)",
        [(1, "Hello world"), (2, "Invoice attached"), (3, "snippet text"), (4, "Old news")],
    )
    con.executemany(
        "INSERT INTO addresses (ROWID, address, comment) VALUES (?,?,?)",
        [(1, "alice@example.com", "Alice"), (2, "bob@icloud.com", "Bob")],
    )
    con.executemany(
        "INSERT INTO mailboxes (ROWID, url, total_count, unread_count) VALUES (?,?,?,?)",
        [
            (1, "imap://AAA11111-UUID/INBOX", 10, 2),
            (2, "imap://BBB22222-UUID/%5BGmail%5D/All%20Mail", 100, 5),
        ],
    )
    now = int(time.time())
    con.executemany(
        "INSERT INTO messages (ROWID, message_id, sender, subject, summary, date_received, mailbox, read, deleted) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (1, 100, 1, 1, None, now, 1, 0, 0),       # unread hello
            (2, 101, 2, 2, None, now - 100, 2, 1, 0),  # read invoice, gmail
            (3, 102, 1, 4, None, now - 200, 1, 0, 1),   # deleted — excluded
            (4, 103, 2, 1, None, now - 50, 1, 0, 0),    # unread hello from bob
        ],
    )
    con.commit()
    con.close()


@pytest.fixture
def envelope(tmp_path, monkeypatch):
    p = tmp_path / "Envelope Index"
    build_envelope(str(p))
    monkeypatch.setattr(m, "ENVELOPE_PATH", str(p))
    return p


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["mail-search", *argv])
    m.main()


@pytest.fixture(autouse=True)
def _clean_log_env(monkeypatch):
    monkeypatch.delenv("MAIL_SEARCH_LOG", raising=False)
    monkeypatch.delenv("IMESSAGE_SEARCH_LOG", raising=False)


def test_parse_limit():
    assert m.parse_limit("3") == 3
    assert m.parse_limit(None) == 40
    with pytest.raises(SystemExit):
        m.parse_limit("x")


def test_fmt_ts_and_short_mailbox():
    assert m.fmt_ts(None).startswith("?")
    assert m.fmt_ts(10**20) == str(10**20)  # OverflowError/OSError path
    assert "INBOX" in m.short_mailbox("imap://ABCDEF12-UUID/INBOX")
    assert "Gmail" in m.short_mailbox("imap://ABCDEF12-UUID/%5BGmail%5D/All%20Mail")
    assert m.short_mailbox(None) == "?"
    assert m.short_mailbox("weird") == "weird"


def test_display_from_and_sanitize():
    assert "Alice" in m.display_from("a@b.com", "Alice")
    assert m.display_from("a@b.com", None) == "a@b.com"
    assert m.display_from(None, None) == "?"
    assert "\x00" not in m.sanitize("hi\x00there")
    assert m.sanitize("a\tb") == "a\tb"          # tab kept
    assert m.sanitize("") == ""
    assert m.sanitize(None) == ""


def test_connect_failure(monkeypatch):
    monkeypatch.setattr(m, "ENVELOPE_PATH", "/nonexistent/Envelope Index")
    with pytest.raises(SystemExit) as e:
        m.connect_envelope()
    assert "Full Disk Access" in str(e.value)


def test_main_no_args():
    with pytest.raises(SystemExit):
        run_main(pytest.MonkeyPatch())


def test_main_unknown(envelope, monkeypatch):
    with pytest.raises(SystemExit):
        run_main(monkeypatch, "frob")


def test_recent_excludes_deleted(envelope, monkeypatch, capsys):
    run_main(monkeypatch, "recent", "10")
    out = capsys.readouterr().out
    assert "Hello world" in out
    assert "Invoice attached" in out
    assert "Old news" not in out  # deleted


def test_unread(envelope, monkeypatch, capsys):
    run_main(monkeypatch, "unread")
    out = capsys.readouterr().out
    assert "Hello world" in out
    assert "Invoice attached" not in out  # read


def test_text(envelope, monkeypatch, capsys):
    run_main(monkeypatch, "text", "invoice")
    assert "Invoice attached" in capsys.readouterr().out


def test_from(envelope, monkeypatch, capsys):
    run_main(monkeypatch, "from", "alice")
    out = capsys.readouterr().out
    assert "Alice" in out
    assert "Bob" not in out


def test_account_gmail(envelope, monkeypatch, capsys):
    run_main(monkeypatch, "account", "Gmail")
    out = capsys.readouterr().out
    assert "Invoice attached" in out
    # hello-on-inbox should not appear
    assert out.count("Hello world") == 0


def test_mailboxes(envelope, monkeypatch, capsys):
    run_main(monkeypatch, "mailboxes")
    out = capsys.readouterr().out
    assert "INBOX" in out
    assert "unread" in out


def test_text_missing_term(envelope, monkeypatch):
    with pytest.raises(SystemExit):
        run_main(monkeypatch, "text")


def test_from_missing(envelope, monkeypatch):
    with pytest.raises(SystemExit):
        run_main(monkeypatch, "from")


def test_account_missing(envelope, monkeypatch):
    with pytest.raises(SystemExit):
        run_main(monkeypatch, "account")


def test_logging_flags(envelope, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="mail_search")
    run_main(monkeypatch, "-vv", "recent", "1")
    assert any("run: recent" in r.message for r in caplog.records)


def test_resolve_log_level_env(monkeypatch):
    monkeypatch.setenv("MAIL_SEARCH_LOG", "DEBUG")
    assert m._resolve_log_level([]) == logging.DEBUG
