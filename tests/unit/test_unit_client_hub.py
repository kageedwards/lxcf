"""Unit tests for hub-aware Client routing.

Tests join/leave with hub parameter, local-only fallback,
multi-hub tracking, and hub send routing.

Requirements: 6.1, 6.2, 6.3, 6.4, 3.1, 3.3
"""

from unittest.mock import MagicMock, patch

from lxcf.client import Client
from lxcf.protocol import derive_channel_hash


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HUB_A = b"\x11" * 16
HUB_B = b"\x22" * 16


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hub_client(nick: str = "tester") -> Client:
    """Create a Client with a mock router for hub-aware testing."""
    mock_dest = MagicMock()
    mock_dest.hash = b"\xAA" * 16
    mock_router = MagicMock()
    mock_router.register_delivery_identity.return_value = mock_dest
    mock_router.register_delivery_callback = MagicMock()
    return Client(router=mock_router, destination=mock_dest, nick=nick)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_join_with_hub_sets_channel_attributes():
    """Join with a hub hash sets ch.hub_hash and ch.channel_hash correctly."""
    client = _make_hub_client()
    ch = client.join("#relay", hub=HUB_A, announce=False)

    assert ch.hub_hash == HUB_A
    assert ch.channel_hash == derive_channel_hash("#relay")


def test_join_without_hub_leaves_hub_hash_none():
    """Join without hub parameter leaves hub_hash as None, channel_hash still computed."""
    client = _make_hub_client()
    ch = client.join("#local", announce=False)

    assert ch.hub_hash is None
    assert ch.channel_hash == derive_channel_hash("#local")


def test_join_with_hub_populates_channel_hash_to_cid():
    """Join with hub populates _channel_hash_to_cid mapping."""
    client = _make_hub_client()
    ch = client.join("#mapped", hub=HUB_A, announce=False)

    expected_hash = derive_channel_hash("#mapped")
    assert client._channel_hash_to_cid[expected_hash] == f"#mapped@{HUB_A.hex()[:8]}"


def test_join_with_hub_sends_join_via_hub():
    """Join with hub and announce=True calls _send_to_hub."""
    client = _make_hub_client()
    with patch.object(client, "_send_to_hub") as mock_hub:
        client.join("#hubbed", hub=HUB_A, announce=True)

    mock_hub.assert_called_once()


def test_join_without_hub_does_not_send():
    """Join without hub and announce=True does not call _send_to_hub (no hub to send to)."""
    client = _make_hub_client()
    with patch.object(client, "_send_to_hub") as mock_hub:
        client.join("#local", announce=True)

    mock_hub.assert_not_called()


def test_multi_hub_tracking():
    """Join two channels on different hubs, each tracks its own hub_hash and channel_hash."""
    client = _make_hub_client()
    ch_a = client.join("#alpha", hub=HUB_A, announce=False)
    ch_b = client.join("#beta", hub=HUB_B, announce=False)

    assert ch_a.hub_hash == HUB_A
    assert ch_b.hub_hash == HUB_B
    assert ch_a.channel_hash == derive_channel_hash("#alpha")
    assert ch_b.channel_hash == derive_channel_hash("#beta")
    assert ch_a.channel_hash != ch_b.channel_hash


def test_cid_includes_hub_hash():
    """CID for hub-based channel is #name@hubhash[:8]."""
    client = _make_hub_client()
    ch = client.join("#mesh", hub=HUB_A, announce=False)
    assert ch._cid == f"#mesh@{HUB_A.hex()[:8]}"


def test_cid_without_hub_is_bare_name():
    """CID for local channel is just the channel name."""
    client = _make_hub_client()
    ch = client.join("#local", announce=False)
    assert ch._cid == "#local"


def test_leave_with_hub_sends_leave_via_hub():
    """Leave a hub-routed channel with announce=True calls _send_to_hub."""
    client = _make_hub_client()
    client.join("#leaving", hub=HUB_A, announce=False)

    with patch.object(client, "_send_to_hub") as mock_hub:
        client.leave(f"#leaving@{HUB_A.hex()[:8]}", announce=True)

    mock_hub.assert_called_once()


def test_leave_cleans_up_channel_hash_to_cid():
    """Leave a hub-routed channel removes its entry from _channel_hash_to_cid."""
    client = _make_hub_client()
    ch = client.join("#cleanup", hub=HUB_A, announce=False)

    ch_hash = ch.channel_hash
    assert ch_hash in client._channel_hash_to_cid

    client.leave(ch._cid, announce=False)
    assert ch_hash not in client._channel_hash_to_cid


from lxcf.envelope import ChannelEnvelope
from lxcf.message import LXCFMessage
from lxcf.protocol import FIELD_CUSTOM_TYPE, FIELD_CUSTOM_DATA


# ---------------------------------------------------------------------------
# Inbound dispatch helpers
# ---------------------------------------------------------------------------

def _make_mock_lxmf_envelope(channel_hash, source_hash, inner_msg):
    """Create a mock LXMF message containing a ChannelEnvelope."""
    inner_fields = inner_msg.to_fields()
    envelope = ChannelEnvelope(
        channel_hash=channel_hash,
        source_hash=source_hash,
        custom_type=inner_fields[FIELD_CUSTOM_TYPE],
        custom_data=inner_fields[FIELD_CUSTOM_DATA],
    )
    mock_msg = MagicMock()
    mock_msg.fields = envelope.to_fields()
    return mock_msg


# ---------------------------------------------------------------------------
# Inbound dispatch tests
# ---------------------------------------------------------------------------

def test_inbound_envelope_dispatches_to_correct_channel():
    """Inbound Channel Envelope with matching channel_hash dispatches to the correct channel."""
    client = _make_hub_client()
    ch = client.join("#relay", hub=HUB_A, announce=False)

    inner = LXCFMessage.chat(nick="remote_user", channel="#relay", body="hello mesh")
    mock_msg = _make_mock_lxmf_envelope(
        channel_hash=derive_channel_hash("#relay"),
        source_hash=b"\xBB" * 16,
        inner_msg=inner,
    )

    client._on_lxmf_delivery(mock_msg)

    assert len(ch.history) == 1
    assert ch.history[0].body == "hello mesh"
    assert ch.history[0].nick == "remote_user"


def test_inbound_envelope_discards_unknown_channel_hash():
    """Inbound Channel Envelope with unknown channel_hash is silently discarded."""
    client = _make_hub_client()
    ch = client.join("#relay", hub=HUB_A, announce=False)

    inner = LXCFMessage.chat(nick="remote_user", channel="#relay", body="lost message")
    mock_msg = _make_mock_lxmf_envelope(
        channel_hash=b"\xFF" * 16,
        source_hash=b"\xBB" * 16,
        inner_msg=inner,
    )

    client._on_lxmf_delivery(mock_msg)

    assert len(ch.history) == 0


def test_inbound_envelope_suppresses_self_echo():
    """Inbound Channel Envelope with source_hash == own hash is suppressed (self-echo)."""
    client = _make_hub_client()
    ch = client.join("#relay", hub=HUB_A, announce=False)

    inner = LXCFMessage.chat(nick="tester", channel="#relay", body="my own echo")
    mock_msg = _make_mock_lxmf_envelope(
        channel_hash=derive_channel_hash("#relay"),
        source_hash=b"\xAA" * 16,
        inner_msg=inner,
    )

    client._on_lxmf_delivery(mock_msg)

    assert len(ch.history) == 0


def test_inbound_envelope_updates_member_tracking():
    """Inbound JOIN envelope updates channel member tracking with nick and source_hash."""
    client = _make_hub_client()
    ch = client.join("#relay", hub=HUB_A, announce=False)

    inner = LXCFMessage.join(nick="newcomer", channel="#relay")
    mock_msg = _make_mock_lxmf_envelope(
        channel_hash=derive_channel_hash("#relay"),
        source_hash=b"\xCC" * 16,
        inner_msg=inner,
    )

    client._on_lxmf_delivery(mock_msg)

    assert "newcomer" in ch.members
    assert ch.member_hashes["newcomer"] == b"\xCC" * 16
