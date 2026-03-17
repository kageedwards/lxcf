"""Unit tests for lxcf.util — nick_with_hash, format_irc_style, MessageDeduplicator."""

import time
import pytest

from lxcf.util import nick_with_hash, format_irc_style, MessageDeduplicator
from lxcf.message import LXCFMessage
from lxcf.protocol import MessageType


# ------------------------------------------------------------------
# nick_with_hash: bytes vs bytearray produce identical results
# ------------------------------------------------------------------

def test_nick_with_hash_bytes_and_bytearray_identical():
    h = b"\xab\xcd" + b"\x00" * 14
    assert nick_with_hash("alice", bytes(h)) == nick_with_hash("alice", bytearray(h))


def test_nick_with_hash_format():
    h = b"\xde\xad" + b"\x00" * 14
    result = nick_with_hash("bob", h)
    assert result == "bob~dead"
    assert result.count("~") == 1


# ------------------------------------------------------------------
# format_irc_style: each message type contains timestamp and nick
# ------------------------------------------------------------------

MSG_TYPE_CASES = [
    (MessageType.MESSAGE, {"channel": "#c", "body": "hi"}),
    (MessageType.EMOTE,   {"channel": "#c", "body": "waves"}),
    (MessageType.JOIN,    {"channel": "#c"}),
    (MessageType.LEAVE,   {"channel": "#c"}),
    (MessageType.TOPIC,   {"channel": "#c", "body": "new topic"}),
    (MessageType.PRIVMSG, {"body": "secret"}),
    (MessageType.ANNOUNCE, {}),
]


@pytest.mark.parametrize("msg_type,kwargs", MSG_TYPE_CASES)
def test_format_irc_style_contains_timestamp_and_nick(msg_type, kwargs):
    msg = LXCFMessage(msg_type, "alice", **kwargs)
    output = format_irc_style(msg)
    ts = time.strftime("%H:%M", time.localtime(msg.timestamp))
    assert ts in output
    assert "alice" in output


# ------------------------------------------------------------------
# MessageDeduplicator TTL expiry
# ------------------------------------------------------------------

def test_dedup_ttl_expiry():
    """After TTL expires and prune runs, message is no longer duplicate."""
    dedup = MessageDeduplicator(ttl=10.0)

    assert dedup.is_duplicate("msg1") is False
    assert dedup.is_duplicate("msg1") is True

    # Simulate TTL expiry by backdating the timestamp
    dedup._seen["msg1"] = time.time() - 20.0
    # Force prune by setting max_size to 0
    dedup._max_size = 0
    dedup._prune()

    assert dedup.is_duplicate("msg1") is False
