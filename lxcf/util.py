"""
LXCF Utilities — helpers for nicknames, deduplication, and formatting.
"""

import hashlib
import time


def nick_with_hash(nick: str, identity_hash: bytes) -> str:
    """Return ``nick~abcd`` where the suffix is derived from the identity hash."""
    short = identity_hash[:2].hex() if isinstance(identity_hash, (bytes, bytearray)) else str(identity_hash)[:4]
    return f"{nick}~{short}"


class MessageDeduplicator:
    """
    Tracks recently seen message hashes to suppress duplicates,
    which is important in mesh topologies where the same message
    can arrive via multiple paths.
    """

    def __init__(self, ttl: float = 300.0, max_size: int = 4096):
        self._seen: dict[str, float] = {}
        self._ttl = ttl
        self._max_size = max_size

    def is_duplicate(self, msg_id: str) -> bool:
        """Return True if *msg_id* was already seen within the TTL window."""
        self._prune()
        if msg_id in self._seen:
            return True
        self._seen[msg_id] = time.time()
        return False

    def _prune(self):
        now = time.time()
        if len(self._seen) > self._max_size:
            cutoff = now - self._ttl
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}

    @staticmethod
    def hash_message(msg_dict: dict) -> str:
        """Produce a short hex digest from a serialised stanza dict."""
        raw = str(sorted(msg_dict.items())).encode()
        return hashlib.sha256(raw).hexdigest()[:16]


def format_irc_style(msg) -> str:
    """Pretty-print an LXCFMessage in classic IRC log format."""
    from lxcf.protocol import MessageType
    ts = time.strftime("%H:%M", time.localtime(msg.timestamp))
    if msg.type == MessageType.MESSAGE:
        return f"[{ts}] <{msg.nick}> {msg.body}"
    elif msg.type == MessageType.EMOTE:
        return f"[{ts}] * {msg.nick} {msg.body}"
    elif msg.type == MessageType.JOIN:
        return f"[{ts}] --> {msg.nick} joined {msg.channel}"
    elif msg.type == MessageType.LEAVE:
        return f"[{ts}] <-- {msg.nick} left {msg.channel}"
    elif msg.type == MessageType.TOPIC:
        return f"[{ts}] {msg.nick} set topic: {msg.body}"
    elif msg.type == MessageType.PRIVMSG:
        return f"[{ts}] [{msg.nick}] {msg.body}"
    elif msg.type == MessageType.ANNOUNCE:
        return f"[{ts}] * {msg.nick} is online"
    else:
        return f"[{ts}] ({msg.type}) {msg.nick}: {msg.body or ''}"
