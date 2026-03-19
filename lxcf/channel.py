"""
LXCF Channel — represents a named group conversation.

All channel traffic is routed through hubs via LXMF SINGLE
destinations wrapped in Channel Envelopes.  The Channel itself
is a local bookkeeping object that tracks members, topic, and
recent history.

The Channel never imports RNS directly.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from lxcf.message import LXCFMessage

if TYPE_CHECKING:
    from lxcf.client import Client


class Channel:
    """
    A named channel that tracks members, topic, and recent history.

    Members are keyed by destination hash (bytes).  Nick is aesthetic
    metadata stored alongside each member entry.

    Parameters
    ----------
    name : str
        Channel name, conventionally prefixed with ``#``.
    client : Client
        The owning LXCF client instance.
    """

    def __init__(self, name: str, client: "Client"):
        self.name = name
        self._client = client
        self.topic: str | None = None

        # Primary member registry: dest_hash -> {nick, last_seen}
        self._members: dict[bytes, dict] = {}

        # Legacy convenience views (nick-keyed) — kept in sync for
        # display code that iterates by nick.
        self.members: dict[str, float] = {}        # nick -> last_seen
        self.member_hashes: dict[str, bytes] = {}   # nick -> dest_hash

        self.history: list[LXCFMessage] = []
        self._max_history = 256
        self.hub_hash: bytes | None = None
        self.channel_hash: bytes = b""
        self.key: bytes | None = None  # symmetric passphrase key for private channels

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def send(self, body: str, **kw) -> LXCFMessage:
        """Send a chat message to this channel."""
        msg = LXCFMessage.chat(self._client.nick, self.name, body, **kw)
        self._client._send_to_channel(self, msg)
        return msg

    def emote(self, body: str, **kw) -> LXCFMessage:
        """Send a /me action to this channel."""
        msg = LXCFMessage.emote(self._client.nick, self.name, body, **kw)
        self._client._send_to_channel(self, msg)
        return msg

    def set_topic(self, topic: str) -> LXCFMessage:
        """Set the channel topic."""
        msg = LXCFMessage.topic(self._client.nick, self.name, topic)
        self.topic = topic
        self._client._send_to_channel(self, msg)
        return msg

    # ------------------------------------------------------------------
    # Member lookup helpers
    # ------------------------------------------------------------------

    def nick_for_hash(self, dest_hash: bytes) -> str | None:
        """Return the nick for a destination hash, or None."""
        entry = self._members.get(dest_hash)
        return entry["nick"] if entry else None

    def hash_for_nick(self, nick: str) -> bytes | None:
        """Return the destination hash for a nick, or None."""
        return self.member_hashes.get(nick)

    def is_member(self, dest_hash: bytes) -> bool:
        """Return True if dest_hash is a current member."""
        return dest_hash in self._members

    # ------------------------------------------------------------------
    # Internal bookkeeping
    # ------------------------------------------------------------------

    def _sync_legacy(self, nick: str, dest_hash: bytes | None, ts: float):
        """Keep the legacy nick-keyed dicts in sync with _members."""
        self.members[nick] = ts
        if dest_hash:
            self.member_hashes[nick] = dest_hash

    def _record(self, msg: LXCFMessage, source_hash: bytes | None = None):
        """Append a message to local history and update member tracking."""
        self.history.append(msg)
        if len(self.history) > self._max_history:
            self.history = self.history[-self._max_history:]

        ts = msg.timestamp
        if source_hash:
            # Update primary registry
            old_entry = self._members.get(source_hash)
            if old_entry and old_entry["nick"] != msg.nick:
                # Nick changed — remove old nick from legacy dicts
                self.members.pop(old_entry["nick"], None)
                self.member_hashes.pop(old_entry["nick"], None)
            self._members[source_hash] = {"nick": msg.nick, "last_seen": ts}
            self._sync_legacy(msg.nick, source_hash, ts)
        else:
            # Local message (no source_hash) — update by nick only
            self.members[msg.nick] = ts

    def _member_join(self, nick: str, ts: float | None = None, source_hash: bytes | None = None):
        t = ts or time.time()
        if source_hash:
            # If this dest_hash was known under a different nick, clean up
            old_entry = self._members.get(source_hash)
            if old_entry and old_entry["nick"] != nick:
                self.members.pop(old_entry["nick"], None)
                self.member_hashes.pop(old_entry["nick"], None)
            self._members[source_hash] = {"nick": nick, "last_seen": t}
        self._sync_legacy(nick, source_hash, t)

    def _member_leave(self, nick: str, source_hash: bytes | None = None):
        # Prefer dest_hash for removal
        if source_hash and source_hash in self._members:
            entry = self._members.pop(source_hash)
            self.members.pop(entry["nick"], None)
            self.member_hashes.pop(entry["nick"], None)
        elif nick:
            # Fallback: remove by nick
            h = self.member_hashes.pop(nick, None)
            self.members.pop(nick, None)
            if h:
                self._members.pop(h, None)

    def _member_nick_change(self, source_hash: bytes, new_nick: str, ts: float | None = None):
        """Update a member's nick by destination hash."""
        t = ts or time.time()
        old_entry = self._members.get(source_hash)
        old_nick = old_entry["nick"] if old_entry else None

        # Clean up old nick from legacy dicts
        if old_nick and old_nick != new_nick:
            self.members.pop(old_nick, None)
            self.member_hashes.pop(old_nick, None)

        self._members[source_hash] = {"nick": new_nick, "last_seen": t}
        self._sync_legacy(new_nick, source_hash, t)
        return old_nick

    def __repr__(self):
        mode = "hub" if self.hub_hash else "local"
        return f"<Channel {self.name!r} members={len(self._members)} {mode}>"
