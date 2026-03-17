"""
LXCF Message — packs/unpacks LXCF stanzas into LXMF Fields dicts
using the standard FIELD_CUSTOM_TYPE / FIELD_CUSTOM_DATA mechanism.
"""

import time
from lxcf.protocol import (
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    FIELD_CUSTOM_TYPE,
    FIELD_CUSTOM_DATA,
    MessageType,
)


class LXCFMessage:
    __slots__ = (
        "type", "nick", "channel", "body",
        "thread", "ref", "timestamp", "extra",
    )

    def __init__(
        self,
        msg_type: str,
        nick: str,
        channel: str | None = None,
        body: str | None = None,
        thread: str | None = None,
        ref: str | None = None,
        timestamp: float | None = None,
        extra: dict | None = None,
    ):
        if msg_type not in MessageType.ALL:
            raise ValueError(f"Unknown LXCF message type: {msg_type}")
        self.type = msg_type
        self.nick = nick
        self.channel = channel
        self.body = body
        self.thread = thread
        self.ref = ref
        self.timestamp = timestamp or time.time()
        self.extra = extra or {}

    def to_fields(self) -> dict:
        """Return a dict for passing as fields= to LXMF.LXMessage()."""
        stanza: dict = {
            "v": PROTOCOL_VERSION,
            "t": self.type,
            "n": self.nick,
        }
        if self.channel is not None:
            stanza["c"] = self.channel
        if self.body is not None:
            stanza["b"] = self.body
        if self.thread is not None:
            stanza["th"] = self.thread
        if self.ref is not None:
            stanza["r"] = self.ref
        if self.extra:
            stanza["x"] = self.extra
        return {
            FIELD_CUSTOM_TYPE: PROTOCOL_NAME,
            FIELD_CUSTOM_DATA: stanza,
        }

    @classmethod
    def from_fields(cls, fields: dict) -> "LXCFMessage":
        """Reconstruct an LXCFMessage from an LXMF fields dictionary."""
        ctype = fields.get(FIELD_CUSTOM_TYPE)
        if ctype != PROTOCOL_NAME:
            raise ValueError(f"Not an LXCF payload (custom_type={ctype!r})")
        stanza = fields.get(FIELD_CUSTOM_DATA)
        if stanza is None:
            raise ValueError("LXCF custom_type present but no custom_data")
        return cls(
            msg_type=stanza["t"],
            nick=stanza["n"],
            channel=stanza.get("c"),
            body=stanza.get("b"),
            thread=stanza.get("th"),
            ref=stanza.get("r"),
            timestamp=stanza.get("ts"),
            extra=stanza.get("x", {}),
        )

    @staticmethod
    def is_lxcf(fields: dict) -> bool:
        """Return True if an LXMF fields dict contains an LXCF stanza."""
        return fields.get(FIELD_CUSTOM_TYPE) == PROTOCOL_NAME

    # Convenience constructors

    @classmethod
    def chat(cls, nick, channel, body, **kw):
        return cls(MessageType.MESSAGE, nick, channel=channel, body=body, **kw)

    @classmethod
    def privmsg(cls, nick, body, **kw):
        return cls(MessageType.PRIVMSG, nick, body=body, **kw)

    @classmethod
    def join(cls, nick, channel, **kw):
        return cls(MessageType.JOIN, nick, channel=channel, **kw)

    @classmethod
    def leave(cls, nick, channel, **kw):
        return cls(MessageType.LEAVE, nick, channel=channel, **kw)

    @classmethod
    def emote(cls, nick, channel, body, **kw):
        return cls(MessageType.EMOTE, nick, channel=channel, body=body, **kw)

    @classmethod
    def announce(cls, nick, channels=None, **kw):
        extra = kw.pop("extra", {})
        if channels:
            extra["channels"] = channels
        return cls(MessageType.ANNOUNCE, nick, extra=extra, **kw)

    @classmethod
    def topic(cls, nick, channel, body, **kw):
        return cls(MessageType.TOPIC, nick, channel=channel, body=body, **kw)

    # @classmethod
    # def reaction(cls, nick, ref, body, channel=None, **kw):
    #     return cls(MessageType.REACTION, nick, channel=channel, body=body, ref=ref, **kw)

    def __repr__(self):
        parts = [f"type={self.type!r}", f"nick={self.nick!r}"]
        if self.channel:
            parts.append(f"channel={self.channel!r}")
        if self.body:
            preview = self.body[:40] + ("\u2026" if len(self.body) > 40 else "")
            parts.append(f"body={preview!r}")
        return f"<LXCFMessage {' '.join(parts)}>"
