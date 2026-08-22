"""Closed-loop iMessage sandbox — fake chat.db, no Apple, no network."""
from __future__ import annotations

from pathlib import Path

import pytest

from imessage_sim.blobs import DECODED, archive_attributed, blob
from imessage_sim.kinds import KINDS, Kind
from imessage_sim.run import evaluate_kind, main, run_all
from imessage_sim.world import TAPBACKS, World, ns_ago


def test_all_catalog_kinds_pass(tmp_path):
    rows = run_all(tmp_path)
    failed = [name for name, ok, _ in rows if not ok]
    assert failed == [], failed
    assert len(rows) == len(KINDS)


def test_evaluate_kind_reports_missing(tmp_path):
    def build(w: World) -> None:
        w.post(handle="+1", from_me=True, text="only this")

    kind = Kind("x", build, ("NOT PRESENT",), (), "neg")
    ok, detail = evaluate_kind(kind, tmp_path)
    assert ok is False
    assert "missing" in detail


def test_evaluate_kind_reports_leak(tmp_path):
    def build(w: World) -> None:
        w.post(handle="+1", from_me=True, text="secret-leak-token")

    kind = Kind("x", build, (), ("secret-leak-token",), "neg")
    ok, detail = evaluate_kind(kind, tmp_path)
    assert ok is False
    assert "leaked" in detail


def test_world_handle_and_chat_reuse(tmp_path):
    w = World(tmp_path / "w.db")
    a = w.handle("+1555")
    assert w.handle("+1555") == a
    c = w.chat("dm")
    assert w.chat("dm") == c
    w.close()


def test_tapback_unknown_kind(tmp_path):
    w = World(tmp_path / "w.db")
    w.post(handle="+1", from_me=False, text="t", guid="G")
    with pytest.raises(ValueError):
        w.tapback("G", "not-a-kind", handle="+1", from_me=True)
    w.close()


def test_ns_ago_positive():
    assert ns_ago(0) > 0
    assert ns_ago(10) < ns_ago(0)


def test_blob_keys_roundtrip():
    assert len(blob("SHORT")) > 10
    assert DECODED["ATTACH"] == "[attachment]"


def test_archive_attributed_without_pyobjc(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "Foundation", None)
    # Force re-entry of import: call should return None or bytes if already imported
    out = archive_attributed("hello")
    assert out is None or isinstance(out, bytes)


def test_archive_attributed_none_data(monkeypatch):
    import sys
    import types

    class Attr:
        @staticmethod
        def alloc():
            class A:
                def initWithString_(self, t):
                    return self
            return A()

    class Arch:
        @staticmethod
        def archivedDataWithRootObject_(s):
            return None

    fake = types.SimpleNamespace(NSAttributedString=Attr, NSArchiver=Arch)
    monkeypatch.setitem(sys.modules, "Foundation", fake)
    assert archive_attributed("x") is None

    class Arch2:
        @staticmethod
        def archivedDataWithRootObject_(s):
            return b"streamtyped-fake"

    fake2 = types.SimpleNamespace(NSAttributedString=Attr, NSArchiver=Arch2)
    monkeypatch.setitem(sys.modules, "Foundation", fake2)
    assert archive_attributed("x") == b"streamtyped-fake"


def test_cli_main_ok(tmp_path, capsys):
    rc = main(["--tmp", str(tmp_path), "--out", str(tmp_path / "r.tsv")])
    assert rc == 0
    text = (tmp_path / "r.tsv").read_text(encoding="utf-8")
    assert "kinds_failed\t0" in text


def test_cli_main_stdout(tmp_path, capsys):
    rc = main(["--tmp", str(tmp_path)])
    assert rc == 0
    assert "kinds_failed" in capsys.readouterr().out


def test_soak_hermetic(tmp_path):
    from imessage_sim.soak import soak, main as soak_main
    stats = soak(rounds=3, live=False, tmp=tmp_path / "a", snapshot=False)
    assert stats["ok"] is True
    assert stats["rounds"] == 3
    stats2 = soak(rounds=2, live=False, tmp=tmp_path / "b", snapshot=True)
    assert stats2["ok"] is True
    rc = soak_main(["--tmp", str(tmp_path / "c"), "--rounds", "2"])
    assert rc == 0
    with pytest.raises(SystemExit):
        soak_main(["--rounds", "0"])


def test_soak_locked_and_other(monkeypatch):
    from imessage_sim import soak as soakmod

    def locked(limit=20):
        raise SystemExit("database is locked")

    monkeypatch.setattr(soakmod, "_recent_once", locked)
    stats = soakmod.soak(rounds=2, live=True, tmp=None, snapshot=False)
    assert stats["locked"] == 2
    assert stats["ok"] is False

    def other(limit=20):
        raise SystemExit("Full Disk Access")

    monkeypatch.setattr(soakmod, "_recent_once", other)
    stats2 = soakmod.soak(rounds=1, live=True, tmp=None, snapshot=False)
    assert stats2["other_errors"] == 1

    def empty(limit=20):
        raise SystemExit()

    monkeypatch.setattr(soakmod, "_recent_once", empty)
    stats3 = soakmod.soak(rounds=1, live=True, tmp=None, snapshot=False)
    assert stats3["locked"] == 0

    monkeypatch.setenv("IMESSAGE_SEARCH_SNAPSHOT", "0")
    def ok(limit=20):
        return None
    monkeypatch.setattr(soakmod, "_recent_once", ok)
    soakmod.soak(rounds=1, live=True, tmp=None, snapshot=True)
    assert __import__("os").environ.get("IMESSAGE_SEARCH_SNAPSHOT") == "0"


def test_soak_main_prints(tmp_path, capsys):
    from imessage_sim.soak import main as soak_main
    rc = soak_main(["--tmp", str(tmp_path / "s"), "--rounds", "2", "--snapshot"])
    assert rc == 0
    assert "p50=" in capsys.readouterr().out


def test_cli_main_nonzero_on_failure(tmp_path, monkeypatch):
    def boom(tmp: Path):
        return [("x", False, "missing hello")]

    monkeypatch.setattr("imessage_sim.run.run_all", boom)
    rc = main(["--tmp", str(tmp_path)])
    assert rc == 1


def test_every_tapback_code_is_documented():
    assert TAPBACKS["loved"] == 2000
    assert TAPBACKS["liked"] == 2001
    assert TAPBACKS["emphasized"] == 2004
    assert set(TAPBACKS) >= {"loved", "liked", "custom", "sticker_react"}


def test_world_edit_and_unsend(tmp_path):
    w = World(tmp_path / "w.db")
    mid = w.post(handle="+1", from_me=True, text="orig")
    w.edit(mid, "new")
    w.unsend(mid)
    row = w.con.execute("SELECT text, date_edited, date_retracted FROM message WHERE ROWID=?", (mid,)).fetchone()
    assert row[0] == "new"
    assert row[1] is not None and row[2] is not None
    w.close()


def test_attachment_without_text_or_blob_gets_placeholder(tmp_path):
    w = World(tmp_path / "w.db")
    mid = w.post(handle="+1", from_me=True, attachment="/fake/x.jpg")
    row = w.con.execute("SELECT text, cache_has_attachments, date_retracted FROM message WHERE ROWID=?", (mid,)).fetchone()
    assert row[0] == "￼"
    assert row[1] == 1
    w.post(handle="+1", from_me=False, text="gone", retracted=True)
    n = w.con.execute("SELECT COUNT(*) FROM message WHERE date_retracted IS NOT NULL").fetchone()[0]
    assert n == 1
    w.close()
