"""Fake Messages.app: write a chat.db the reader can open.

Nothing here talks to Apple, the network, or real contacts.
"Send" means INSERT a row the way Messages would. "Receive" is the same
with is_from_me=0. Tapbacks / stickers / breadcrumbs use associated_message_type
values observed on a live Ventura+ database (see kinds.py).
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from imessage_sim.blobs import OBJ_REPLACEMENT

APPLE_EPOCH = 978307200

# Live-db taxonomy (associated_message_type). 0 = ordinary user message.
TAPBACKS = {
    "loved": 2000,
    "liked": 2001,
    "disliked": 2002,
    "laughed": 2003,
    "emphasized": 2004,
    "questioned": 2005,
    "custom": 2006,       # "Reacted 💪🏻 to …"
    "sticker_react": 2007,
}
# Removals are +1000 (3000 = un-loved, …).
TAPBACK_REMOVALS = {f"un{name}": code + 1000 for name, code in TAPBACKS.items()}
PLUGIN_BREADCRUMB = 3     # workout completed, etc. — not a user message
PLUGIN_BALLOON = 2        # Safety Monitor / some extensions
STICKER = 1000
POLL = 4000


def ns_ago(days: float) -> int:
    return int((time.time() - days * 86400 - APPLE_EPOCH) * 1_000_000_000)


class World:
    """A disposable Messages universe on disk."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self.con = sqlite3.connect(self.path)
        self.con.executescript(
            """
            CREATE TABLE handle (
                ROWID INTEGER PRIMARY KEY,
                id TEXT
            );
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY,
                guid TEXT,
                date INTEGER,
                is_from_me INTEGER,
                is_read INTEGER,
                text TEXT,
                attributedBody BLOB,
                handle_id INTEGER,
                associated_message_type INTEGER,
                associated_message_guid TEXT,
                reply_to_guid TEXT,
                cache_has_attachments INTEGER DEFAULT 0,
                balloon_bundle_id TEXT,
                date_edited INTEGER,
                date_retracted INTEGER
            );
            CREATE TABLE chat (
                ROWID INTEGER PRIMARY KEY,
                chat_identifier TEXT,
                room_name TEXT,
                guid TEXT
            );
            CREATE TABLE chat_message_join (
                chat_id INTEGER,
                message_id INTEGER
            );
            CREATE TABLE attachment (
                ROWID INTEGER PRIMARY KEY,
                filename TEXT,
                mime_type TEXT,
                transfer_name TEXT
            );
            CREATE TABLE message_attachment_join (
                message_id INTEGER,
                attachment_id INTEGER
            );
            """
        )
        self._handles: dict[str, int] = {}
        self._next_msg = 1
        self._next_att = 1
        self._chats: dict[str, int] = {}

    def close(self) -> None:
        self.con.commit()
        self.con.close()

    def handle(self, ident: str) -> int:
        if ident in self._handles:
            return self._handles[ident]
        hid = len(self._handles) + 1
        self.con.execute("INSERT INTO handle (ROWID, id) VALUES (?,?)", (hid, ident))
        self._handles[ident] = hid
        return hid

    def chat(self, ident: str, *, room: str | None = None) -> int:
        if ident in self._chats:
            return self._chats[ident]
        cid = len(self._chats) + 1
        guid = f"iMessage;+;{ident}"
        self.con.execute(
            "INSERT INTO chat (ROWID, chat_identifier, room_name, guid) VALUES (?,?,?,?)",
            (cid, ident, room or ident, guid),
        )
        self._chats[ident] = cid
        return cid

    def post(
        self,
        *,
        handle: str,
        from_me: bool,
        text: str | None = None,
        blob: bytes | None = None,
        amt: int = 0,
        associated_guid: str | None = None,
        reply_to: str | None = None,
        days_ago: float = 0,
        read: bool = True,
        chat: str = "dm",
        balloon: str | None = None,
        attachment: str | None = None,
        mime: str = "image/jpeg",
        edited: bool = False,
        retracted: bool = False,
        guid: str | None = None,
    ) -> int:
        """Insert one message. Returns ROWID. This is the sandbox 'send'/'receive'."""
        mid = self._next_msg
        self._next_msg += 1
        guid = guid or f"SIM-{mid:06d}"
        hid = self.handle(handle)
        cid = self.chat(chat)
        has_att = 1 if attachment else 0
        if attachment and text is None and blob is None:
            text = OBJ_REPLACEMENT
        self.con.execute(
            """INSERT INTO message (
                ROWID, guid, date, is_from_me, is_read, text, attributedBody,
                handle_id, associated_message_type, associated_message_guid,
                reply_to_guid, cache_has_attachments, balloon_bundle_id,
                date_edited, date_retracted
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mid, guid, ns_ago(days_ago), int(from_me), int(read), text, blob,
                hid, amt, associated_guid, reply_to, has_att, balloon,
                ns_ago(days_ago) if edited else None,
                ns_ago(days_ago) if retracted else None,
            ),
        )
        self.con.execute(
            "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?,?)",
            (cid, mid),
        )
        if attachment:
            aid = self._next_att
            self._next_att += 1
            self.con.execute(
                "INSERT INTO attachment (ROWID, filename, mime_type, transfer_name) VALUES (?,?,?,?)",
                (aid, attachment, mime, Path(attachment).name),
            )
            self.con.execute(
                "INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (?,?)",
                (mid, aid),
            )
        return mid

    def tapback(self, on_guid: str, kind: str, *, handle: str, from_me: bool, days_ago: float = 0) -> int:
        if kind not in TAPBACKS and kind not in TAPBACK_REMOVALS:
            raise ValueError(f"unknown tapback kind {kind!r}")
        amt = TAPBACKS.get(kind) or TAPBACK_REMOVALS[kind]
        label = {
            "loved": "Loved “sim”",
            "liked": "Liked “sim”",
            "disliked": "Disliked “sim”",
            "laughed": "Laughed at “sim”",
            "emphasized": "Emphasized “sim”",
            "questioned": "Questioned “sim”",
            "custom": "Reacted 💪 to “sim”",
            "sticker_react": "Reacted with a sticker to “sim”",
        }.get(kind, f"Removed a reaction from “sim”" if kind.startswith("un") else kind)
        return self.post(
            handle=handle, from_me=from_me, text=label, amt=amt,
            associated_guid=f"p:0/{on_guid}", days_ago=days_ago, chat="dm",
        )
