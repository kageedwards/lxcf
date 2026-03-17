"""Unit tests for lxcf.channel.Channel."""

from lxcf.client import Client
from lxcf.message import LXCFMessage


# ------------------------------------------------------------------
# History capping (Property 13)
# ------------------------------------------------------------------

def test_history_capping():
    """Recording more than max_history messages caps the list."""
    client = Client(nick="test")
    ch = client.join("#test", announce=False)
    n = ch._max_history + 50

    for i in range(n):
        msg = LXCFMessage.chat("test", "#test", f"msg-{i}")
        ch._record(msg)

    assert len(ch.history) == ch._max_history
    assert ch.history[-1].body == f"msg-{n - 1}"


def test_history_under_max():
    """Recording fewer than max_history keeps all messages."""
    client = Client(nick="test")
    ch = client.join("#test", announce=False)

    for i in range(10):
        msg = LXCFMessage.chat("test", "#test", f"msg-{i}")
        ch._record(msg)

    assert len(ch.history) == 10


# ------------------------------------------------------------------
# Member tracking
# ------------------------------------------------------------------

def test_member_join_adds_to_members_and_hashes():
    client = Client(nick="test")
    ch = client.join("#test", announce=False)
    source = b"\xaa" * 16

    ch._member_join("bob", source_hash=source)

    assert "bob" in ch.members
    assert ch.member_hashes["bob"] == source


def test_member_leave_removes_from_members_and_hashes():
    client = Client(nick="test")
    ch = client.join("#test", announce=False)
    source = b"\xbb" * 16

    ch._member_join("carol", source_hash=source)
    assert "carol" in ch.members

    ch._member_leave("carol")
    assert "carol" not in ch.members
    assert "carol" not in ch.member_hashes


# ------------------------------------------------------------------
# send / emote / set_topic record in history and emit events
# ------------------------------------------------------------------

def test_send_records_in_history_and_emits(event_log):
    client = Client(nick="alice")
    client.events.on("message", lambda ch, msg: event_log.append(("message", msg.body)))
    ch = client.join("#test", announce=False)

    ch.send("hello")

    assert any(m.body == "hello" for m in ch.history)
    assert ("message", "hello") in event_log


def test_emote_records_in_history_and_emits(event_log):
    client = Client(nick="alice")
    client.events.on("emote", lambda ch, msg: event_log.append(("emote", msg.body)))
    ch = client.join("#test", announce=False)

    ch.emote("waves")

    assert any(m.body == "waves" for m in ch.history)
    assert ("emote", "waves") in event_log


def test_set_topic_records_in_history_and_emits(event_log):
    client = Client(nick="alice")
    client.events.on("topic", lambda ch, msg: event_log.append(("topic", msg.body)))
    ch = client.join("#test", announce=False)

    ch.set_topic("new topic")

    assert ch.topic == "new topic"
    assert any(m.body == "new topic" for m in ch.history)
    assert ("topic", "new topic") in event_log
