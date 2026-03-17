"""
LXCF Channel — represents a named group conversation.

When running over LXMF, each channel maps to a Reticulum GROUP
destination.  The group's symmetric key is derived deterministically
from the channel name (for open channels) or from a caller-supplied
key (for private channels).

The Channel never imports RNS directly — it receives a pre-built
group destination from the Client, which handles all LXMF/RNS
bootstrapping.
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

    Parameters
    ----------
    name : str
        Channel name, conventionally prefixed with ``#``.
    client : Client
        The owning LXCF client instance.
    group_destination : object | None
        An ``RNS.Destination`` of type GROUP, or None in local mode.
    """

    def __init__(self, name: str, client: "Client", group_destination=None):
        self.name = name
        self._client = client
        self.destination = group_destination  # RNS.Destination (GROUP) or None
        self.topic: str | None = None
        self.members: dict[str, float] = {}   # nick -> last_seen timestamp
        self.member_hashes: dict[str, bytes] = {}  # nick -> identity hash
        self.history: list[LXCFMessage] = []
        self._max_history = 256

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
    # Internal bookkeeping
    # ------------------------------------------------------------------

    def _record(self, msg: LXCFMessage, source_hash: bytes | None = None):
        """Append a message to local history and update member tracking."""
        self.history.append(msg)
        if len(self.history) > self._max_history:
            self.history = self.history[-self._max_history:]
        self.members[msg.nick] = msg.timestamp
        if source_hash:
            self.member_hashes[msg.nick] = source_hash

    def _member_join(self, nick: str, ts: float | None = None, source_hash: bytes | None = None):
        self.members[nick] = ts or time.time()
        if source_hash:
            self.member_hashes[nick] = source_hash

    def _member_leave(self, nick: str):
        self.members.pop(nick, None)
        self.member_hashes.pop(nick, None)

    def __repr__(self):
        mode = "group" if self.destination else "local"
        return f"<Channel {self.name!r} members={len(self.members)} {mode}>"
