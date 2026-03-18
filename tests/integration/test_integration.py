"""
Integration tests for lxcf — verifies cross-component interactions
between Client, Channel, EventBus, and LXCFMessage in local mode.

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5
Property 16: Message Routing Isolation
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lxcf.client import Client
from lxcf.message import LXCFMessage
from lxcf.protocol import MessageType


# ------------------------------------------------------------------
# Req 10.1: Join → Send → Leave lifecycle fires events in order
# ------------------------------------------------------------------

def test_join_send_leave_lifecycle_event_order():
    """Events fire in correct order: join, message, leave."""
    log = []
    client = Client(nick="alice")
    client.events.on("join", lambda ch, nick: log.append(("join", nick)))
    client.events.on("message", lambda ch, msg: log.append(("message", msg.body)))
    client.events.on("leave", lambda ch, nick: log.append(("leave", nick)))

    ch = client.join("#test")
    ch.send("hello")
    client.leave("#test")

    assert log == [("join", "alice"), ("message", "hello"), ("leave", "alice")]


def test_lifecycle_channel_state_transitions():
    """Channel appears in dict after join, disappears after leave."""
    client = Client(nick="alice")
    assert "#test" not in client.channels

    client.join("#test", announce=False)
    assert "#test" in client.channels

    client.leave("#test", announce=False)
    assert "#test" not in client.channels


# ------------------------------------------------------------------
# Req 10.2 / Property 16: Multi-channel message routing isolation
# ------------------------------------------------------------------

def test_multi_channel_routing_isolation():
    """Messages route to the correct channel only."""
    client = Client(nick="alice")
    ch_a = client.join("#alpha", announce=False)
    ch_b = client.join("#beta", announce=False)

    ch_a.send("msg-alpha")
    ch_b.send("msg-beta")

    alpha_bodies = [m.body for m in ch_a.history]
    beta_bodies = [m.body for m in ch_b.history]

    assert "msg-alpha" in alpha_bodies
    assert "msg-beta" not in alpha_bodies
    assert "msg-beta" in beta_bodies
    assert "msg-alpha" not in beta_bodies


def test_multi_channel_events_carry_correct_channel():
    """Each message event carries the channel it was sent to."""
    log = []
    client = Client(nick="alice")
    client.events.on("message", lambda ch, msg: log.append((ch.name, msg.body)))

    ch_a = client.join("#alpha", announce=False)
    ch_b = client.join("#beta", announce=False)

    ch_a.send("hello-a")
    ch_b.send("hello-b")

    assert ("#alpha", "hello-a") in log
    assert ("#beta", "hello-b") in log



@given(n_channels=st.integers(min_value=2, max_value=6))
@settings(max_examples=50)
def test_routing_isolation_property(n_channels):
    """Property 16: sending to one channel never leaks to another."""
    client = Client(nick="alice")
    channels = []
    for i in range(n_channels):
        ch = client.join(f"#ch{i}", announce=False)
        channels.append(ch)

    # Send a unique message to each channel
    for i, ch in enumerate(channels):
        ch.send(f"only-{i}")

    # Each channel's history should contain only its own message
    for i, ch in enumerate(channels):
        bodies = [m.body for m in ch.history]
        assert f"only-{i}" in bodies
        for j in range(n_channels):
            if j != i:
                assert f"only-{j}" not in bodies


# ------------------------------------------------------------------
# Req 10.3: _dispatch_inbound routes various message types correctly
# ------------------------------------------------------------------

def test_dispatch_inbound_message_type():
    """MESSAGE type routes to channel and fires 'message' event."""
    log = []
    client = Client(nick="alice")
    ch = client.join("#test", announce=False)
    client.events.on("message", lambda ch, msg: log.append(("message", ch.name, msg.body)))

    msg = LXCFMessage.chat("bob", "#test", "hi there")
    client._dispatch_inbound(msg, source_hash=b"\x01" * 16)

    assert any(m.body == "hi there" for m in ch.history)
    assert ("message", "#test", "hi there") in log


def test_dispatch_inbound_join_type():
    """JOIN type adds member and fires 'join' event."""
    log = []
    client = Client(nick="alice")
    ch = client.join("#test", announce=False)
    client.events.on("join", lambda ch, nick: log.append(("join", nick)))

    msg = LXCFMessage.join("bob", "#test")
    client._dispatch_inbound(msg, source_hash=b"\x02" * 16)

    assert "bob" in ch.members
    assert ("join", "bob") in log


def test_dispatch_inbound_leave_type():
    """LEAVE type removes member and fires 'leave' event."""
    log = []
    client = Client(nick="alice")
    ch = client.join("#test", announce=False)
    ch._member_join("bob", source_hash=b"\x03" * 16)
    client.events.on("leave", lambda ch, nick: log.append(("leave", nick)))

    msg = LXCFMessage.leave("bob", "#test")
    client._dispatch_inbound(msg)

    assert "bob" not in ch.members
    assert ("leave", "bob") in log


def test_dispatch_inbound_emote_type():
    """EMOTE type records in history and fires 'emote' event."""
    log = []
    client = Client(nick="alice")
    ch = client.join("#test", announce=False)
    client.events.on("emote", lambda ch, msg: log.append(("emote", msg.body)))

    msg = LXCFMessage.emote("bob", "#test", "dances")
    client._dispatch_inbound(msg, source_hash=b"\x04" * 16)

    assert any(m.body == "dances" for m in ch.history)
    assert ("emote", "dances") in log


def test_dispatch_inbound_topic_type():
    """TOPIC type sets channel topic and fires 'topic' event."""
    log = []
    client = Client(nick="alice")
    ch = client.join("#test", announce=False)
    client.events.on("topic", lambda ch, msg: log.append(("topic", msg.body)))

    msg = LXCFMessage.topic("bob", "#test", "new topic")
    client._dispatch_inbound(msg)

    assert ch.topic == "new topic"
    assert ("topic", "new topic") in log


def test_dispatch_inbound_privmsg_type():
    """PRIVMSG type fires 'privmsg' event with source_hash."""
    log = []
    client = Client(nick="alice")
    source = b"\x05" * 16
    client.events.on("privmsg", lambda src, msg: log.append(("privmsg", src, msg.body)))

    msg = LXCFMessage.privmsg("bob", "secret")
    client._dispatch_inbound(msg, source_hash=source)

    assert ("privmsg", source, "secret") in log


def test_dispatch_inbound_nick_type():
    """NICK type updates member list and fires 'nick' event."""
    log = []
    client = Client(nick="alice")
    ch = client.join("#test", announce=False)
    source = b"\x06" * 16
    ch._member_join("bob", source_hash=source)
    client.events.on("nick", lambda old, new: log.append(("nick", old, new)))

    msg = LXCFMessage(MessageType.NICK, "bobby", channel="#test")
    client._dispatch_inbound(msg, source_hash=source)

    assert "bobby" in ch.members
    assert "bob" not in ch.members
    assert ("nick", "bob", "bobby") in log


def test_dispatch_inbound_announce_type():
    """ANNOUNCE type fires 'announce' event."""
    log = []
    client = Client(nick="alice")
    client.events.on("announce", lambda nick, chs: log.append(("announce", nick)))

    msg = LXCFMessage.announce("bob", channels=["#test"])
    client._dispatch_inbound(msg)

    assert ("announce", "bob") in log


# ------------------------------------------------------------------
# Req 10.4: Blocked sender messages are dropped
# ------------------------------------------------------------------

def test_blocked_sender_message_dropped():
    """Messages from blocked senders are not dispatched."""
    log = []
    client = Client(nick="alice")
    ch = client.join("#test", announce=False)
    client.events.on("message", lambda ch, msg: log.append(("message", msg.body)))

    blocked_hash = b"\xbb" * 16
    client.block(blocked_hash)

    # Simulate what _on_lxmf_delivery / _on_group_packet do:
    # check is_blocked before dispatching
    msg = LXCFMessage.chat("evil", "#test", "spam")
    if not client.is_blocked(blocked_hash):
        client._dispatch_inbound(msg, source_hash=blocked_hash)

    assert len(log) == 0
    assert not any(m.body == "spam" for m in ch.history)


def test_blocked_sender_no_event_emitted():
    """No events fire for blocked senders across all event types."""
    log = []
    client = Client(nick="alice")
    client.join("#test", announce=False)

    for evt in ("message", "join", "leave", "emote", "topic", "privmsg", "nick", "announce"):
        client.events.on(evt, lambda *a, _e=evt: log.append(_e))

    blocked_hash = b"\xcc" * 16
    client.block(blocked_hash)

    messages = [
        LXCFMessage.chat("evil", "#test", "spam"),
        LXCFMessage.join("evil", "#test"),
        LXCFMessage.emote("evil", "#test", "lurks"),
    ]

    for msg in messages:
        if not client.is_blocked(blocked_hash):
            client._dispatch_inbound(msg, source_hash=blocked_hash)

    assert len(log) == 0


def test_unblocked_sender_passes_through():
    """Messages from non-blocked senders are dispatched normally."""
    log = []
    client = Client(nick="alice")
    ch = client.join("#test", announce=False)
    client.events.on("message", lambda ch, msg: log.append(("message", msg.body)))

    good_hash = b"\xaa" * 16
    msg = LXCFMessage.chat("bob", "#test", "hello")

    if not client.is_blocked(good_hash):
        client._dispatch_inbound(msg, source_hash=good_hash)

    assert ("message", "hello") in log
    assert any(m.body == "hello" for m in ch.history)



# ------------------------------------------------------------------
# Req 10.5: announce_presence broadcasts to all joined channels
# ------------------------------------------------------------------

def test_announce_presence_broadcasts_to_all_channels():
    """announce_presence records an announce message in every joined channel."""
    client = Client(nick="alice")
    ch_a = client.join("#alpha", announce=False)
    ch_b = client.join("#beta", announce=False)
    ch_c = client.join("#gamma", announce=False)

    client.announce_presence()

    for ch in (ch_a, ch_b, ch_c):
        assert any(m.type == MessageType.ANNOUNCE for m in ch.history)


def test_announce_presence_fires_event():
    """announce_presence emits an 'announce' event."""
    log = []
    client = Client(nick="alice")
    client.events.on("announce", lambda nick, chs: log.append(("announce", nick, chs)))
    client.join("#one", announce=False)
    client.join("#two", announce=False)

    client.announce_presence()

    assert len(log) == 1
    assert log[0][0] == "announce"
    assert log[0][1] == "alice"
    assert set(log[0][2]) == {"#one", "#two"}


# ------------------------------------------------------------------
# Event decorator shortcuts
# ------------------------------------------------------------------

def test_on_message_decorator():
    log = []
    client = Client(nick="alice")

    @client.on_message
    def handler(ch, msg):
        log.append(msg.body)

    ch = client.join("#test", announce=False)
    ch.send("hi")
    assert "hi" in log


def test_on_privmsg_decorator():
    log = []
    client = Client(nick="alice")

    @client.on_privmsg
    def handler(src, msg):
        log.append(msg.body)

    msg = LXCFMessage.privmsg("bob", "secret")
    client._dispatch_inbound(msg, source_hash=b"\x01" * 16)
    assert "secret" in log


def test_on_join_decorator():
    log = []
    client = Client(nick="alice")

    @client.on_join
    def handler(ch, nick):
        log.append(nick)

    client.join("#test", announce=False)
    assert "alice" in log


def test_on_leave_decorator():
    log = []
    client = Client(nick="alice")

    @client.on_leave
    def handler(ch, nick):
        log.append(nick)

    client.join("#test", announce=False)
    client.leave("#test", announce=False)
    assert "alice" in log


def test_on_announce_decorator():
    log = []
    client = Client(nick="alice")

    @client.on_announce
    def handler(nick, chs):
        log.append(nick)

    client.join("#test", announce=False)
    client.announce_presence()
    assert "alice" in log


# ------------------------------------------------------------------
# Nick change across multiple channels
# ------------------------------------------------------------------

def test_nick_change_updates_all_channel_members():
    """change_nick propagates to all joined channels."""
    client = Client(nick="alice")
    ch1 = client.join("#one", announce=False)
    ch2 = client.join("#two", announce=False)
    ch3 = client.join("#three", announce=False)

    client.change_nick("alicia", announce=False)

    for ch in (ch1, ch2, ch3):
        assert "alicia" in ch.members
        assert "alice" not in ch.members


def test_nick_change_fires_event():
    """change_nick emits a 'nick' event with old and new names."""
    log = []
    client = Client(nick="alice")
    client.events.on("nick", lambda old, new: log.append((old, new)))
    client.join("#test", announce=False)

    client.change_nick("alicia", announce=False)

    assert ("alice", "alicia") in log


def test_nick_change_preserves_member_hashes():
    """change_nick moves the identity hash from old nick to new nick."""
    client = Client(nick="alice")
    ch = client.join("#test", announce=False)
    # Manually set a hash for alice to verify it transfers
    fake_hash = b"\xdd" * 16
    ch.member_hashes["alice"] = fake_hash

    client.change_nick("alicia", announce=False)

    assert ch.member_hashes.get("alicia") == fake_hash
    assert "alice" not in ch.member_hashes


# ------------------------------------------------------------------
# Cross-component: dispatch_inbound to multiple channels
# ------------------------------------------------------------------

def test_dispatch_inbound_routes_to_correct_channel_by_name():
    """Inbound messages route to the channel matching msg.channel."""
    client = Client(nick="alice")
    ch_a = client.join("#alpha", announce=False)
    ch_b = client.join("#beta", announce=False)

    msg = LXCFMessage.chat("bob", "#alpha", "for alpha")
    client._dispatch_inbound(msg, source_hash=b"\x10" * 16)

    assert any(m.body == "for alpha" for m in ch_a.history)
    assert not any(m.body == "for alpha" for m in ch_b.history)


def test_dispatch_inbound_join_leave_cycle():
    """Inbound join then leave updates member list correctly."""
    client = Client(nick="alice")
    ch = client.join("#test", announce=False)

    join_msg = LXCFMessage.join("bob", "#test")
    client._dispatch_inbound(join_msg, source_hash=b"\x20" * 16)
    assert "bob" in ch.members

    leave_msg = LXCFMessage.leave("bob", "#test")
    client._dispatch_inbound(leave_msg)
    assert "bob" not in ch.members
