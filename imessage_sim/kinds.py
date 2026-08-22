"""Catalog of iMessage kinds the sandbox can materialize.

Each kind is a closed-loop experiment: write → read → assert.
`expect_in_recent` encodes the product contract: the CLI shows ordinary
user messages (associated_message_type=0) and hides tapbacks/plugins.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from imessage_sim.blobs import DECODED, blob
from imessage_sim.world import (
    PLUGIN_BALLOON,
    PLUGIN_BREADCRUMB,
    POLL,
    STICKER,
    World,
)


@dataclass(frozen=True)
class Kind:
    name: str
    build: Callable[[World], None]
    expect_in_recent: tuple[str, ...]  # substrings that MUST appear
    forbid_in_recent: tuple[str, ...]  # substrings that MUST NOT appear
    note: str


def _plain_send(w: World) -> None:
    w.post(handle="+15551110001", from_me=True, text="hello from me", days_ago=0)


def _plain_recv(w: World) -> None:
    w.post(handle="+15551110002", from_me=False, text="hello from them", days_ago=0, read=False)


def _blob_body(w: World) -> None:
    w.post(handle="+15551110003", from_me=False, blob=blob("SHORT"), days_ago=0)


def _unicode_blob(w: World) -> None:
    w.post(handle="+15551110004", from_me=False, blob=blob("UNICODE"), days_ago=0)


def _attachment(w: World) -> None:
    w.post(
        handle="+15551110005", from_me=False, blob=blob("ATTACH"),
        attachment="/fake/photo.jpg", days_ago=0,
    )


def _attachment_then_removed_caption(w: World) -> None:
    # Caption stripped to placeholder-only → reader should still show [attachment]
    w.post(
        handle="+15551110006", from_me=True, text="￼",
        attachment="/fake/old.jpg", days_ago=0,
    )


def _reply_thread(w: World) -> None:
    a = w.post(handle="+15551110007", from_me=False, text="original question", guid="ORIG-1", days_ago=1)
    w.post(
        handle="+15551110007", from_me=True, text="threaded reply",
        reply_to="ORIG-1", days_ago=0, guid="REPLY-1",
    )
    _ = a


def _group_chat(w: World) -> None:
    w.post(handle="+15551110008", from_me=False, text="group hello", chat="chat.sim.group", days_ago=0)


def _edited(w: World) -> None:
    w.post(handle="+15551110009", from_me=True, text="edited later", edited=True, days_ago=0)


def _all_tapbacks(w: World) -> None:
    guid = "TGT-1"
    w.post(handle="+15551110010", from_me=False, text="tapback target", guid=guid, days_ago=1)
    for kind in ("loved", "liked", "disliked", "laughed", "emphasized", "questioned", "custom", "sticker_react"):
        w.tapback(guid, kind, handle="+15551110011", from_me=True, days_ago=0)


def _unreact(w: World) -> None:
    guid = "TGT-2"
    w.post(handle="+15551110012", from_me=False, text="unreact target", guid=guid, days_ago=1)
    w.tapback(guid, "loved", handle="+15551110013", from_me=True, days_ago=0.5)
    w.tapback(guid, "unloved", handle="+15551110013", from_me=True, days_ago=0)


def _sticker(w: World) -> None:
    w.post(handle="+15551110014", from_me=False, text="￼", amt=STICKER, days_ago=0)


def _breadcrumb(w: World) -> None:
    w.post(
        handle="+15551110015", from_me=False,
        text="$(kIMTranscriptPluginBreadcrumbTextReceiverIdentifier) completed a workout.",
        amt=PLUGIN_BREADCRUMB, days_ago=0,
    )


def _plugin_balloon(w: World) -> None:
    w.post(
        handle="+15551110016", from_me=True, text="￼", amt=PLUGIN_BALLOON,
        balloon="com.apple.SafetyMonitorApp.SafetyMonitorMessages", days_ago=0,
    )


def _poll(w: World) -> None:
    w.post(handle="+15551110017", from_me=False, text=" ", amt=POLL, balloon="com.apple.messages.Polls", days_ago=0)


def _url_balloon_ordinary(w: World) -> None:
    # URL previews on real DBs are type=0 with balloon_bundle_id set — they SHOULD show.
    w.post(
        handle="+15551110018", from_me=False, text="https://example.com/x",
        balloon="com.apple.messages.URLBalloonProvider", days_ago=0,
    )


KINDS: tuple[Kind, ...] = (
    Kind("plain_send", _plain_send, ("hello from me",), (), "from-me text column"),
    Kind("plain_recv", _plain_recv, ("hello from them",), (), "incoming unread text"),
    Kind("blob_body", _blob_body, (DECODED["SHORT"],), (), "attributedBody typedstream"),
    Kind("unicode_blob", _unicode_blob, ("café",), (), "unicode in blob"),
    Kind("attachment", _attachment, ("[attachment]",), (), "image-only attributedBody"),
    Kind("attachment_placeholder", _attachment_then_removed_caption, ("[attachment]",), (), "U+FFFC-only text"),
    Kind("reply_thread", _reply_thread, ("original question", "threaded reply"), (), "reply_to_guid still type 0"),
    Kind("group_chat", _group_chat, ("group hello",), (), "chat_identifier join"),
    Kind("edited", _edited, ("edited later",), (), "date_edited set; still visible"),
    Kind(
        "all_tapbacks", _all_tapbacks, ("tapback target",),
        ("Loved", "Liked", "Disliked", "Laughed", "Emphasized", "Questioned", "Reacted", "sticker"),
        "2000-2007 must not pollute recent",
    ),
    Kind(
        "unreact", _unreact, ("unreact target",),
        ("Removed a reaction", "Loved"),
        "3000-series removals hidden",
    ),
    Kind("sticker", _sticker, (), ("￼",), "sticker type 1000 hidden"),
    Kind("breadcrumb", _breadcrumb, (), ("workout",), "type 3 plugin breadcrumb hidden"),
    Kind("plugin_balloon", _plugin_balloon, (), (), "type 2 plugin hidden (no user text to forbid)"),
    Kind("poll", _poll, (), (), "type 4000 poll hidden"),
    Kind("url_preview", _url_balloon_ordinary, ("https://example.com/x",), (), "URL balloon on type 0 is a real message"),
)
