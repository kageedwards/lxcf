"""Unit tests for lxcf.client.Client in local mode."""

import pytest
from lxcf.client import Client


# ------------------------------------------------------------------
# Local mode basics
# ------------------------------------------------------------------

def test_local_mode_connected_false(local_client):
    assert local_client.connected is False


def test_local_mode_address_none(local_client):
    assert local_client.address is None


# ------------------------------------------------------------------
# join / leave
# ------------------------------------------------------------------

def test_join_adds_channel_and_emits(event_log):
    client = Client(nick="alice")
    client.events.on("join", lambda ch, nick: event_log.append(("join", nick)))

    ch = client.join("#test", announce=False)

    assert "#test" in client.channels
    assert ("join", "alice") in event_log


def test_leave_removes_channel_and_emits(event_log):
    client = Client(nick="alice")
    client.events.on("leave", lambda ch, nick: event_log.append(("leave", nick)))

    client.join("#test", announce=False)
    assert "#test" in client.channels

    client.leave("#test", announce=False)
    assert "#test" not in client.channels
    assert ("leave", "alice") in event_log


# ------------------------------------------------------------------
# change_nick (Property 15: Nick Change Propagation)
# ------------------------------------------------------------------

def test_change_nick_updates_all_channels():
    client = Client(nick="alice")
    ch1 = client.join("#one", announce=False)
    ch2 = client.join("#two", announce=False)

    client.change_nick("bob", announce=False)

    assert client.nick == "bob"
    for ch in (ch1, ch2):
        assert "bob" in ch.members
        assert "alice" not in ch.members


# ------------------------------------------------------------------
# trust / block
# ------------------------------------------------------------------

def test_trust_adds_to_trusted_removes_from_blocked():
    client = Client(nick="alice")
    h = b"\x01" * 16

    client.block(h)
    assert h in client.blocked

    client.trust(h)
    assert h in client.trusted
    assert h not in client.blocked


def test_block_adds_to_blocked_removes_from_trusted():
    client = Client(nick="alice")
    h = b"\x02" * 16

    client.trust(h)
    assert h in client.trusted

    client.block(h)
    assert h in client.blocked
    assert h not in client.trusted


# ------------------------------------------------------------------
# send on unjoined channel raises ValueError
# ------------------------------------------------------------------

def test_send_unjoined_channel_raises():
    client = Client(nick="alice")
    with pytest.raises(ValueError, match="Not in channel"):
        client.send("#nonexistent", "hello")
