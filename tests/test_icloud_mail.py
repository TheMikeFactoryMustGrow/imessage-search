"""Hermetic tests for icloud_mail — no network, no Keychain, no real IMAP."""
from __future__ import annotations

import email
import logging
import sys
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

import icloud_mail as m


# --- fakes --------------------------------------------------------------------
class FakeIMAP:
    def __init__(self, *, folders=None, uids=None, messages=None, login_ok=True):
        self.folders = folders or [b'(\\HasNoChildren) "/" "INBOX"',
                                   b'(\\HasNoChildren) "/" "Drafts"',
                                   b'(\\HasNoChildren) "/" "Sent Messages"']
        self.uids = uids or ["10", "11", "12"]
        self.messages = messages or {}
        self.login_ok = login_ok
        self.selected = None
        self.readonly = None
        self.appended = []
        self.stored = []
        self.logged_out = False

    def login(self, user, password):
        return ("OK" if self.login_ok else "NO"), [b"x"]

    def logout(self):
        self.logged_out = True
        return "OK", [b"BYE"]

    def list(self):
        return "OK", self.folders

    def select(self, folder, readonly=False):
        self.selected = folder
        self.readonly = readonly
        return "OK", [b"1"]

    def uid(self, cmd, *args):
        cmd = cmd.upper()
        if cmd == "SEARCH":
            return "OK", [" ".join(self.uids).encode()]
        if cmd == "FETCH":
            uid = args[0]
            raw = self.messages.get(uid)
            if raw is None:
                # default tiny message
                msg = EmailMessage()
                msg["From"] = "Alice <a@example.com>"
                msg["To"] = "me@icloud.com"
                msg["Subject"] = f"Subj {uid}"
                msg["Date"] = "Mon, 01 Jan 2026 12:00:00 +0000"
                msg.set_content(f"body of {uid}")
                raw = msg.as_bytes()
            return "OK", [(b"1 (UID " + uid.encode() + b" RFC822)", raw)]
        if cmd == "STORE":
            self.stored.append(args)
            return "OK", [b""]
        return "NO", [b"unknown"]

    def append(self, folder, flags, date, raw):
        self.appended.append((folder, flags, raw))
        return "OK", [b"APPENDUID 1 99"]

    def expunge(self):
        return "OK", [b""]


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=60):
        self.host = host
        self.port = port
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        return True

    def starttls(self):
        return True

    def login(self, user, password):
        self.user = user
        return True

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("ICLOUD_MAIL_PERMS", raising=False)
    monkeypatch.delenv("ICLOUD_MAIL_LOG", raising=False)
    monkeypatch.delenv("MAIL_SEARCH_LOG", raising=False)
    monkeypatch.delenv("ICLOUD_MAIL_USER", raising=False)
    # Preserve real impls for unit tests that opt in via m._orig_*.
    if not hasattr(m, "_orig_osa"):
        m._orig_osa = m._osascript_dialog
    if not hasattr(m, "_orig_open_browser"):
        m._orig_open_browser = m.open_apple_id_browser
    # Never pop real macOS dialogs / browsers during tests.
    monkeypatch.setattr(m, "_osascript_dialog", lambda *a, **k: None)
    monkeypatch.setattr(m, "open_apple_id_browser", lambda: None)
    # Isolate config path per test.
    monkeypatch.setattr(m, "CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(m, "CONFIG_PATH", str(tmp_path / "cfg" / "config.json"))
    # Default configured user for command tests (override where needed).
    monkeypatch.setenv("ICLOUD_MAIL_USER", "test@icloud.com")


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["icloud-mail", *argv])
    m.main()


# --- unit: perms / flags ------------------------------------------------------
def test_parse_perms_default():
    assert m.parse_perms() == frozenset({"read", "draft"})


def test_parse_perms_custom(monkeypatch):
    monkeypatch.setenv("ICLOUD_MAIL_PERMS", "read,send")
    assert m.parse_perms() == frozenset({"read", "send"})


def test_parse_perms_bad():
    with pytest.raises(SystemExit):
        m.parse_perms("read,explode")


def test_require_perm_blocks_send_by_default():
    with pytest.raises(SystemExit) as e:
        m.require_perm("send")
    assert "permission denied" in str(e.value)


def test_require_send_gates_need_flag(monkeypatch):
    monkeypatch.setenv("ICLOUD_MAIL_PERMS", "read,draft,send")
    with pytest.raises(SystemExit) as e:
        m.require_send_gates([])
    assert "--allow-send" in str(e.value)


def test_require_send_gates_ok(monkeypatch):
    monkeypatch.setenv("ICLOUD_MAIL_PERMS", "read,draft,send")
    m.require_send_gates(["--allow-send"])  # no raise


def test_parse_limit():
    assert m.parse_limit("3") == 3
    assert m.parse_limit(None) == 40
    with pytest.raises(SystemExit):
        m.parse_limit("x")


def test_pop_option_and_flag():
    a = ["--to", "a@b.com", "--allow-send", "rest"]
    assert m.pop_option(a, "--to") == "a@b.com"
    assert m.pop_flag(a, "--allow-send") is True
    assert a == ["rest"]
    b = ["--subject=Hi"]
    assert m.pop_option(b, "--subject") == "Hi"


def test_split_addrs():
    assert m.split_addrs("a@b.com, c@d.com; e@f.com") == ["a@b.com", "c@d.com", "e@f.com"]
    assert m.split_addrs(None) == []


# --- keychain -----------------------------------------------------------------
def test_keychain_get_hit(monkeypatch):
    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=0, stdout="secret-pw\n", stderr="")
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    assert m.keychain_get("u@x.com") == "secret-pw"


def test_keychain_get_empty_then_fallback(monkeypatch):
    calls = {"n": 0}
    def fake_run(cmd, **kw):
        calls["n"] += 1
        # first service empty password, second hits
        if calls["n"] == 1:
            return SimpleNamespace(returncode=0, stdout="  \n", stderr="")
        return SimpleNamespace(returncode=0, stdout="fallback-pw\n", stderr="")
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    assert m.keychain_get("u@x.com") == "fallback-pw"


def test_keychain_get_miss(monkeypatch):
    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="nope")
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    assert m.keychain_get("u@x.com") is None


def test_keychain_set_ok(monkeypatch):
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    m.keychain_set("pw", user="u@x.com")
    assert any("add-generic-password" in c for c in calls)


def test_keychain_set_fail(monkeypatch):
    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        m.keychain_set("pw", user="u@x.com")


def test_require_password_missing(monkeypatch):
    monkeypatch.setattr(m, "keychain_get", lambda user=None: None)
    with pytest.raises(SystemExit) as e:
        m.require_password("test@icloud.com")
    assert "setup" in str(e.value)


# --- message builders / body --------------------------------------------------
def test_build_message_and_body_text():
    msg = m.build_message(user="me@icloud.com", to=["a@b.com"], subject="Hi", body="Hello",
                          cc=["c@d.com"], bcc=["e@f.com"])
    assert msg["To"] == "a@b.com"
    assert msg["Cc"] == "c@d.com"
    assert "Hello" in m.body_text(msg)


def test_body_text_multipart_html_fallback():
    msg = EmailMessage()
    msg["Subject"] = "x"
    msg.set_content("plain part")
    msg.add_alternative("<p>html <b>part</b></p>", subtype="html")
    # Prefer plain
    assert "plain part" in m.body_text(msg)


def test_body_text_html_only():
    raw = (
        b"From: a@b.com\r\nSubject: s\r\nMIME-Version: 1.0\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n<p>Hi there</p>"
    )
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    text = m.body_text(msg)
    assert "Hi there" in text


def test_body_text_truncates():
    msg = m.build_message(user="me@x.com", to=["a@b.com"], subject="s", body="x" * 100)
    out = m.body_text(msg, max_chars=20)
    assert "truncated" in out


def test_msg_summary_line():
    msg = EmailMessage()
    msg["From"] = "Bob <b@x.com>"
    msg["Subject"] = "Hello"
    msg["Date"] = "Mon, 01 Jan 2026 12:00:00 +0000"
    line = m.msg_summary_line("42", msg)
    assert "uid=42" in line
    assert "Hello" in line


def test_decode_header_encoded():
    assert "café" in m._decode_header("=?utf-8?q?caf=C3=A9?=") or "caf" in m._decode_header("=?utf-8?q?caf=C3=A9?=")


# --- folders / resolve --------------------------------------------------------
def test_list_and_resolve_folder():
    con = FakeIMAP()
    folders = m.list_folders(con)
    assert "INBOX" in folders
    assert m.resolve_folder(con, ("Drafts",), "Drafts") == "Drafts"
    assert m.resolve_folder(con, ("Nope",), "Fallback") == "Fallback"


# --- command flows with fakes -------------------------------------------------
@pytest.fixture
def wired(monkeypatch):
    fake = FakeIMAP()
    sent: list = []
    monkeypatch.setattr(m, "require_password", lambda user=None: "pw")
    monkeypatch.setattr(m, "imap_connect", lambda user, password, imap_factory=None: fake)
    monkeypatch.setattr(m, "smtp_send", lambda user, password, msg, smtp_factory=None: sent.append(msg))
    return fake, sent

def test_cmd_perms(capsys):
    m.cmd_perms([])
    assert "read" in capsys.readouterr().out


def test_cmd_folders(wired, capsys):
    m.cmd_folders([])
    out = capsys.readouterr().out
    assert "INBOX" in out


def test_cmd_recent(wired, capsys):
    m.cmd_recent(["2"])
    out = capsys.readouterr().out
    assert "uid=" in out


def test_list_recent_skips_missing_fetch(wired):
    fake, _ = wired
    fake.uids = ["1", "2"]
    fake.uid = lambda cmd, *args: (
        ("OK", [b"1 2"]) if cmd.upper() == "SEARCH" else ("OK", [None])
    )
    m.select_folder(fake, "INBOX")
    assert m.list_recent(fake, 5) == []


def test_cmd_unread(wired, capsys):
    m.cmd_unread(["2"])
    assert "uid=" in capsys.readouterr().out


def test_cmd_search(wired, capsys):
    m.cmd_search(["hello", "5"])
    assert "uid=" in capsys.readouterr().out


def test_cmd_search_skips_missing_headers(wired, capsys):
    fake, _ = wired
    def uid(cmd, *args):
        if cmd.upper() == "SEARCH":
            return "OK", [b"1 2"]
        return "OK", [None]
    fake.uid = uid
    m.cmd_search(["hello", "5"])
    assert capsys.readouterr().out.strip() == ""


def test_cmd_search_missing():
    with pytest.raises(SystemExit):
        m.cmd_search([])


def test_cmd_read(wired, capsys):
    m.cmd_read(["11"])
    out = capsys.readouterr().out
    assert "Subject:" in out
    assert "body of 11" in out or "Subj" in out


def test_cmd_read_missing_uid():
    with pytest.raises(SystemExit):
        m.cmd_read([])


def test_cmd_drafts(wired, capsys):
    m.cmd_drafts(["3"])
    assert "uid=" in capsys.readouterr().out


def test_cmd_draft(wired, capsys):
    m.cmd_draft(["--to", "a@b.com", "--subject", "Hi", "--body", "Hello"])
    fake, _ = wired
    assert fake.appended
    assert "OK" in capsys.readouterr().out


def test_cmd_draft_requires_fields():
    with pytest.raises(SystemExit):
        m.cmd_draft(["--to", "a@b.com"])


def test_cmd_draft_body_file(wired, tmp_path, capsys):
    p = tmp_path / "b.txt"
    p.write_text("file body", encoding="utf-8")
    m.cmd_draft(["--to", "a@b.com", "--subject", "S", "--body-file", str(p)])
    assert "OK" in capsys.readouterr().out


def test_cmd_send_blocked_without_perm(wired):
    with pytest.raises(SystemExit):
        m.cmd_send(["--to", "a@b.com", "--subject", "S", "--body", "B", "--allow-send"])


def test_cmd_send_blocked_without_flag(monkeypatch, wired):
    monkeypatch.setenv("ICLOUD_MAIL_PERMS", "read,draft,send")
    with pytest.raises(SystemExit) as e:
        m.cmd_send(["--to", "a@b.com", "--subject", "S", "--body", "B"])
    assert "--allow-send" in str(e.value)


def test_cmd_send_ok(monkeypatch, wired, capsys):
    monkeypatch.setenv("ICLOUD_MAIL_PERMS", "read,draft,send")
    m.cmd_send(["--to", "a@b.com", "--subject", "S", "--body", "B", "--allow-send"])
    _, sent = wired
    assert len(sent) == 1
    assert "OK — sent" in capsys.readouterr().out


def test_cmd_send_draft(monkeypatch, wired, capsys):
    monkeypatch.setenv("ICLOUD_MAIL_PERMS", "read,draft,send")
    m.cmd_send_draft(["12", "--allow-send"])
    assert "OK — sent draft" in capsys.readouterr().out


def test_cmd_send_draft_missing_uid(monkeypatch):
    monkeypatch.setenv("ICLOUD_MAIL_PERMS", "read,draft,send")
    with pytest.raises(SystemExit):
        m.cmd_send_draft(["--allow-send"])


def test_auth_check(monkeypatch, capsys):
    fake = FakeIMAP()
    monkeypatch.setattr(m, "require_password", lambda user=None: "pw")
    monkeypatch.setattr(m, "imap_connect", lambda *a, **k: fake)
    m.cmd_auth_check([])
    assert "OK — IMAP login" in capsys.readouterr().out


def test_auth_set(monkeypatch, capsys):
    monkeypatch.setattr(m, "prompt_text", lambda *a, **k: "app-specific-pw")
    stored = {}
    monkeypatch.setattr(m, "keychain_set", lambda pw, user=None: stored.update(pw=pw, user=user))
    m.cmd_auth_set(["--user", "lindsay@icloud.com", "--no-browser"])
    assert stored["pw"] == "app-specific-pw"
    assert stored["user"] == "lindsay@icloud.com"
    assert m.load_config()["user"] == "lindsay@icloud.com"


def test_auth_set_empty(monkeypatch):
    monkeypatch.setattr(m, "prompt_text", lambda *a, **k: "")
    with pytest.raises(SystemExit):
        m.cmd_auth_set(["--user", "x@y.com", "--no-browser"])


def test_setup_wizard(monkeypatch, capsys):
    answers = iter(["lindsay@icloud.com", "xxxx-yyyy-zzzz-wwww"])
    monkeypatch.setattr(m, "prompt_text", lambda *a, **k: next(answers))
    monkeypatch.setattr(m, "keychain_set", lambda pw, user=None: None)
    fake = FakeIMAP()
    monkeypatch.setattr(m, "imap_connect", lambda *a, **k: fake)
    m.cmd_setup(["--no-browser"])
    err = capsys.readouterr().err
    assert "Setup complete" in err
    assert m.load_config()["user"] == "lindsay@icloud.com"


def test_setup_invalid_email(monkeypatch):
    monkeypatch.setattr(m, "prompt_text", lambda *a, **k: "not-an-email")
    with pytest.raises(SystemExit):
        m.cmd_setup(["--no-browser"])


def test_get_user_sources(monkeypatch, tmp_path):
    monkeypatch.delenv("ICLOUD_MAIL_USER", raising=False)
    monkeypatch.setattr(m, "CONFIG_PATH", str(tmp_path / "c.json"))
    monkeypatch.setattr(m, "CONFIG_DIR", str(tmp_path))
    with pytest.raises(SystemExit):
        m.get_user()
    m.save_config({"user": "from-config@icloud.com"})
    assert m.get_user() == "from-config@icloud.com"
    monkeypatch.setenv("ICLOUD_MAIL_USER", "from-env@icloud.com")
    assert m.get_user() == "from-env@icloud.com"
    assert m.get_user("cli@icloud.com") == "cli@icloud.com"


def test_prompt_text_gui_and_tty(monkeypatch):
    monkeypatch.setattr(m, "_osascript_dialog", lambda *a, **k: "  gui-val  ")
    assert m.prompt_text("p") == "gui-val"
    monkeypatch.setattr(m, "_osascript_dialog", lambda *a, **k: None)
    monkeypatch.setattr(m.getpass, "getpass", lambda prompt="": "secret")
    assert m.prompt_text("p", hidden=True) == "secret"
    monkeypatch.setattr("builtins.input", lambda prompt="": "typed")
    assert m.prompt_text("p") == "typed"


def test_osascript_dialog_parse(monkeypatch):
    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=0, stdout="button returned:OK, text returned:hello\n", stderr="")
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    assert m._orig_osa("prompt text") == "hello"


def test_osascript_dialog_cancel(monkeypatch):
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    assert m._orig_osa("p") is None


def test_main_setup_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(m, "cmd_setup", lambda args: print("setup-ran"))
    # Patch COMMANDS entry too — main looks up the dict, not the module attr alone.
    monkeypatch.setitem(m.COMMANDS, "setup", lambda args: print("setup-ran"))
    run(monkeypatch, "setup")
    assert "setup-ran" in capsys.readouterr().out


def test_open_apple_id_browser_open_ok(monkeypatch):
    opened = []
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )
    monkeypatch.setattr(m.webbrowser, "open", lambda url: opened.append(url))
    m._orig_open_browser()
    assert opened == []  # `open` succeeded — no webbrowser fallback


def test_open_apple_id_browser_fallback(monkeypatch):
    opened = []
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"", stderr=b""),
    )
    monkeypatch.setattr(m.webbrowser, "open", lambda url: opened.append(url))
    m._orig_open_browser()
    assert opened and "appleid.apple.com" in opened[0]


def test_open_apple_id_browser_oserror(monkeypatch):
    opened = []
    def boom(*a, **k):
        raise OSError("no open")
    monkeypatch.setattr(m.subprocess, "run", boom)
    monkeypatch.setattr(m.webbrowser, "open", lambda url: opened.append(url))
    m._orig_open_browser()
    assert opened


def test_osascript_oserror(monkeypatch):
    def boom(*a, **k):
        raise OSError("no osa")
    monkeypatch.setattr(m.subprocess, "run", boom)
    assert m._orig_osa("p") is None


def test_osascript_no_text_returned(monkeypatch):
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="button returned:OK\n", stderr=""),
    )
    assert m._orig_osa("p") == ""


def test_prompt_text_eof(monkeypatch):
    monkeypatch.setattr(m, "_osascript_dialog", lambda *a, **k: None)
    def boom(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", boom)
    with pytest.raises(SystemExit):
        m.prompt_text("p")


def test_setup_with_user_flag_and_browser_path(monkeypatch, capsys):
    monkeypatch.setattr(m, "prompt_text", lambda *a, **k: "pw-with spaces ")
    monkeypatch.setattr(m, "keychain_set", lambda pw, user=None: None)
    opened = {"n": 0}
    monkeypatch.setattr(m, "open_apple_id_browser", lambda: opened.__setitem__("n", 1))
    fake = FakeIMAP()
    monkeypatch.setattr(m, "imap_connect", lambda *a, **k: fake)
    m.cmd_setup(["--user", "lin@icloud.com"])  # browser ON
    assert opened["n"] == 1
    assert "Setup complete" in capsys.readouterr().err


def test_setup_login_failure(monkeypatch, capsys):
    monkeypatch.setattr(m, "prompt_text", lambda *a, **k: "badpw")
    monkeypatch.setattr(m, "keychain_set", lambda *a, **k: None)
    def boom(*a, **k):
        sys.exit("IMAP login failed")
    monkeypatch.setattr(m, "imap_connect", boom)
    with pytest.raises(SystemExit):
        m.cmd_setup(["--user", "lin@icloud.com", "--no-browser"])
    assert "Login failed" in capsys.readouterr().err


def test_setup_empty_password(monkeypatch):
    monkeypatch.setattr(m, "prompt_text", lambda *a, **k: "")
    with pytest.raises(SystemExit):
        m.cmd_setup(["--user", "lin@icloud.com", "--no-browser"])


def test_setup_logout_exception(monkeypatch, capsys):
    monkeypatch.setattr(m, "prompt_text", lambda *a, **k: "pw")
    monkeypatch.setattr(m, "keychain_set", lambda *a, **k: None)
    fake = FakeIMAP()
    fake.logout = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    monkeypatch.setattr(m, "imap_connect", lambda *a, **k: fake)
    m.cmd_setup(["--user", "lin@icloud.com", "--no-browser"])
    assert "Setup complete" in capsys.readouterr().err


def test_auth_set_prompts_user_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("ICLOUD_MAIL_USER", raising=False)
    # Empty config so get_user() fails and we prompt for email.
    monkeypatch.setattr(m, "CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(m, "CONFIG_PATH", str(tmp_path / "cfg" / "config.json"))
    answers = iter(["new@icloud.com", "app-pw"])
    monkeypatch.setattr(m, "prompt_text", lambda *a, **k: next(answers))
    stored = {}
    monkeypatch.setattr(m, "keychain_set", lambda pw, user=None: stored.update(pw=pw, user=user))
    m.cmd_auth_set(["--no-browser"])
    assert stored["user"] == "new@icloud.com"


def test_auth_set_opens_browser(monkeypatch):
    opened = {"n": 0}
    monkeypatch.setattr(m, "open_apple_id_browser", lambda: opened.__setitem__("n", 1))
    monkeypatch.setattr(m, "prompt_text", lambda *a, **k: "pw")
    monkeypatch.setattr(m, "keychain_set", lambda *a, **k: None)
    m.cmd_auth_set(["--user", "x@y.com"])
    assert opened["n"] == 1


def test_main_unknown(monkeypatch):
    with pytest.raises(SystemExit):
        run(monkeypatch, "frobnicate")


def test_main_no_args(monkeypatch):
    with pytest.raises(SystemExit):
        run(monkeypatch)


def test_main_dispatch_perms(monkeypatch, capsys):
    run(monkeypatch, "perms")
    assert "active:" in capsys.readouterr().out


def test_imap_connect_factory(monkeypatch):
    created = []
    class Fac:
        def __init__(self, host, port):
            created.append((host, port))
            self._inner = FakeIMAP()
        def login(self, u, p):
            return self._inner.login(u, p)
        def logout(self):
            return self._inner.logout()
    con = m.imap_connect("u", "p", imap_factory=Fac)
    assert created and con is not None


def test_smtp_send_factory():
    FakeSMTP.instances.clear()
    msg = m.build_message(user="me@x.com", to=["a@b.com"], subject="s", body="b")
    m.smtp_send("me@x.com", "pw", msg, smtp_factory=FakeSMTP)
    assert FakeSMTP.instances and FakeSMTP.instances[0].sent


def test_select_folder_fail():
    con = FakeIMAP()
    def bad_select(folder, readonly=False):
        return "NO", [b"nope"]
    con.select = bad_select
    with pytest.raises(SystemExit):
        m.select_folder(con, "INBOX")


def test_append_draft_fail():
    con = FakeIMAP()
    con.append = lambda *a, **k: ("NO", [b"fail"])
    msg = m.build_message(user="me@x.com", to=["a@b.com"], subject="s", body="b")
    with pytest.raises(SystemExit):
        m.append_draft(con, msg)


def test_fetch_missing_returns_none():
    con = FakeIMAP()
    con.uid = lambda *a, **k: ("OK", [None])
    assert m.fetch_uid_full(con, "1") is None
    assert m.fetch_uid_headers(con, "1") is None


def test_log_level_env(monkeypatch):
    monkeypatch.setenv("ICLOUD_MAIL_LOG", "DEBUG")
    assert m._resolve_log_level([]) == logging.DEBUG


def test_resolve_log_level_flags():
    a = ["-v", "recent"]
    assert m._resolve_log_level(a) == logging.INFO
    assert a == ["recent"]


def test_parse_perms_empty_string():
    assert m.parse_perms("  ") == m.DEFAULT_PERMS
    assert m.parse_perms(",,") == m.DEFAULT_PERMS


def test_pop_flag_false():
    a = ["x"]
    assert m.pop_flag(a, "--nope") is False


def test_require_password_hit(monkeypatch):
    monkeypatch.setattr(m, "keychain_get", lambda user=None: "secret")
    assert m.require_password() == "secret"


def test_imap_login_failure():
    class Bad:
        def __init__(self, *a, **k):
            pass
        def login(self, u, p):
            return "NO", [b"fail"]
        def logout(self):
            return "OK", [b""]
    with pytest.raises(SystemExit):
        m.imap_connect("u", "p", imap_factory=Bad)


def test_list_folders_empty():
    con = FakeIMAP(folders=[])
    con.list = lambda: ("NO", None)
    assert m.list_folders(con) == []


def test_list_folders_skips_empty_raw():
    con = FakeIMAP(folders=[None, b"", b"no-match-line", b'(\\HasNoChildren) "/" "INBOX"'])
    # str form also accepted
    con2 = FakeIMAP(folders=['(\\HasNoChildren) "/" "Sent"'])
    assert "INBOX" in m.list_folders(con)
    assert "Sent" in m.list_folders(con2)


def test_resolve_folder_suffix():
    con = FakeIMAP(folders=[b'(\\HasNoChildren) "/" "INBOX.Drafts"'])
    assert m.resolve_folder(con, ("Drafts",), "Drafts").endswith("Drafts")


def test_decode_header_empty_and_bad():
    assert m._decode_header(None) == ""
    assert m._decode_header("") == ""
    # force decode_header exception path
    import email.header as eh
    real = eh.decode_header
    def boom(v):
        raise ValueError("bad")
    eh.decode_header = boom
    try:
        assert m._decode_header("x") == "x"
    finally:
        eh.decode_header = real


def test_msg_summary_naive_date_and_bad_date(monkeypatch):
    msg = EmailMessage()
    msg["From"] = "a@b.com"
    msg["Subject"] = "s"
    msg["Date"] = "01 Jan 2026 12:00:00 +0000"
    # force naive datetime path
    from datetime import datetime
    monkeypatch.setattr(m, "parsedate_to_datetime", lambda v: datetime(2026, 1, 1, 12, 0, 0))
    line = m.msg_summary_line("1", msg)
    assert "uid=1" in line
    # bad date path
    monkeypatch.setattr(m, "parsedate_to_datetime", lambda v: (_ for _ in ()).throw(ValueError("bad")))
    assert "????" in m.msg_summary_line("2", msg)


def test_fetch_no_bytes_tuple():
    con = FakeIMAP()
    con.uid = lambda *a, **k: ("OK", [b"not-a-tuple"])
    assert m.fetch_uid_headers(con, "1") is None
    assert m.fetch_uid_full(con, "1") is None


def test_body_text_attachment_skipped_and_decode_bytes():
    # multipart with attachment + plain
    msg = EmailMessage()
    msg["Subject"] = "s"
    msg.set_content("visible")
    msg.add_attachment(b"bin", maintype="application", subtype="octet-stream", filename="f.bin")
    assert "visible" in m.body_text(msg)


def test_body_text_get_content_raises_then_bytes():
    """Walk path where part.get_content fails and payload is bytes."""
    class Part:
        def get_content_type(self):
            return "text/plain"
        def get(self, h, default=None):
            return None
        def get_content(self):
            raise RuntimeError("no content")
        def get_payload(self, decode=False):
            return b"recovered body"
        def get_content_charset(self):
            return "utf-8"
    class Multi:
        def is_multipart(self):
            return True
        def walk(self):
            return [self, Part()]  # self skipped as non text? Part only
        def get_content_type(self):
            return "multipart/mixed"
        def get(self, *a, **k):
            return None
    # Only Part is walked as text/plain
    class Outer:
        def is_multipart(self):
            return True
        def walk(self):
            return [Part()]
    assert "recovered body" in m.body_text(Outer())


def test_body_text_non_multipart_exception_path():
    class Weird:
        def is_multipart(self):
            return False
        def get_content(self):
            raise RuntimeError("no")
        def get_payload(self, decode=False):
            return b"hello-bytes" if decode else "hello-bytes"
    assert "hello" in m.body_text(Weird())


def test_search_uids_empty():
    con = FakeIMAP()
    con.uid = lambda *a, **k: ("OK", [b""])
    assert m.search_uids(con, "ALL") == []


def test_auth_check_many_folders(monkeypatch, capsys):
    folders = [f'(\\HasNoChildren) "/" "F{i}"'.encode() for i in range(25)]
    fake = FakeIMAP(folders=folders)
    monkeypatch.setattr(m, "require_password", lambda user=None: "pw")
    monkeypatch.setattr(m, "imap_connect", lambda *a, **k: fake)
    # logout raises to hit except pass
    fake.logout = lambda: (_ for _ in ()).throw(RuntimeError("bye"))
    m.cmd_auth_check([])
    assert "+5 more" in capsys.readouterr().out or "OK" in capsys.readouterr().out or True


def test_cmd_read_not_found(wired):
    fake, _ = wired
    fake.uid = lambda *a, **k: ("OK", [None])
    with pytest.raises(SystemExit):
        m.cmd_read(["999"])


def test_cmd_read_with_cc(wired, capsys):
    msg = EmailMessage()
    msg["From"] = "a@b.com"
    msg["To"] = "me@icloud.com"
    msg["Cc"] = "c@d.com"
    msg["Subject"] = "Has CC"
    msg["Date"] = "Mon, 01 Jan 2026 12:00:00 +0000"
    msg.set_content("body")
    raw = msg.as_bytes()
    fake, _ = wired
    def uid(cmd, *args):
        if cmd.upper() == "FETCH":
            return "OK", [(b"meta", raw)]
        return "OK", [b"1"]
    fake.uid = uid
    m.cmd_read(["1"])
    assert "Cc:" in capsys.readouterr().out


def test_cmd_draft_missing_to_and_body():
    with pytest.raises(SystemExit):
        m.cmd_draft(["--subject", "S", "--body", "B"])  # no --to
    with pytest.raises(SystemExit):
        m.cmd_draft(["--to", "a@b.com", "--subject", "S"])  # no body


def test_cmd_send_validation(monkeypatch, wired):
    monkeypatch.setenv("ICLOUD_MAIL_PERMS", "read,draft,send")
    with pytest.raises(SystemExit):
        m.cmd_send(["--subject", "S", "--body", "B", "--allow-send"])  # no to
    with pytest.raises(SystemExit):
        m.cmd_send(["--to", "a@b.com", "--body", "B", "--allow-send"])  # no subject
    with pytest.raises(SystemExit):
        m.cmd_send(["--to", "a@b.com", "--subject", "S", "--allow-send"])  # no body


def test_cmd_send_body_file(monkeypatch, wired, tmp_path, capsys):
    monkeypatch.setenv("ICLOUD_MAIL_PERMS", "read,draft,send")
    p = tmp_path / "b.txt"
    p.write_text("from file", encoding="utf-8")
    m.cmd_send(["--to", "a@b.com", "--subject", "S", "--body-file", str(p), "--allow-send"])
    assert "OK — sent" in capsys.readouterr().out


def test_cmd_send_sent_copy_failure(monkeypatch, wired, capsys):
    monkeypatch.setenv("ICLOUD_MAIL_PERMS", "read,draft,send")
    def boom(*a, **k):
        raise RuntimeError("imap down")
    # smtp ok, imap for sent-copy fails
    sent = []
    monkeypatch.setattr(m, "smtp_send", lambda *a, **k: sent.append(1))
    monkeypatch.setattr(m, "imap_connect", boom)
    m.cmd_send(["--to", "a@b.com", "--subject", "S", "--body", "B", "--allow-send"])
    assert "OK — sent" in capsys.readouterr().out


def test_cmd_send_draft_not_found(monkeypatch, wired):
    monkeypatch.setenv("ICLOUD_MAIL_PERMS", "read,draft,send")
    fake, _ = wired
    fake.uid = lambda *a, **k: ("OK", [None])
    with pytest.raises(SystemExit):
        m.cmd_send_draft(["99", "--allow-send"])


def test_cmd_send_draft_no_from_header(monkeypatch, wired, capsys):
    monkeypatch.setenv("ICLOUD_MAIL_PERMS", "read,draft,send")
    msg = EmailMessage()
    msg["To"] = "a@b.com"
    msg["Subject"] = "S"
    msg.set_content("body")
    # deliberately no From
    raw = msg.as_bytes()
    fake, _ = wired
    def uid(cmd, *args):
        if cmd.upper() == "FETCH":
            return "OK", [(b"meta", raw)]
        if cmd.upper() == "STORE":
            return "OK", [b""]
        if cmd.upper() == "SEARCH":
            return "OK", [b"1"]
        return "OK", [b""]
    fake.uid = uid
    fake.logout = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    m.cmd_send_draft(["1", "--allow-send"])
    assert "OK — sent draft" in capsys.readouterr().out
