"""
Unit tests for Hub.

Validates: Requirements 1.1, 3.5, 4.4, 9.1, 9.2, 9.3, 9.4, 9.5
"""

import sys
from unittest.mock import MagicMock, patch, call

from lxcf.protocol import (
    FIELD_CHANNEL_HASH,
    FIELD_SOURCE_HASH,
    FIELD_CUSTOM_TYPE,
    FIELD_CUSTOM_DATA,
    PROTOCOL_NAME,
    MessageType,
)
from lxcf.envelope import ChannelEnvelope
from lxcf.hub import Hub


# --- Fixed byte values ---
CH_A = b"\xaa" * 16
CH_B = b"\xbb" * 16
CH_C = b"\xcc" * 16
SRC_1 = b"\x11" * 16
SRC_2 = b"\x22" * 16
SRC_3 = b"\x33" * 16


def _make_hub(max_channels=32, max_subscribers_per_channel=32):
    """Create a Hub with mock router/identity."""
    mock_dest = MagicMock()
    mock_dest.hash = b"\x00" * 16

    mock_router = MagicMock()
    mock_router.register_delivery_identity.return_value = mock_dest
    mock_router.register_delivery_callback = MagicMock()
    mock_router.handle_outbound = MagicMock()

    mock_identity = MagicMock()

    hub = Hub(
        router=mock_router,
        identity=mock_identity,
        max_channels=max_channels,
        max_subscribers_per_channel=max_subscribers_per_channel,
    )
    return hub, mock_router, mock_identity, mock_dest


def _join_fields(ch, src, nick="user"):
    """Build raw LXMF fields dict for a JOIN envelope."""
    return {
        FIELD_CHANNEL_HASH: ch,
        FIELD_SOURCE_HASH: src,
        FIELD_CUSTOM_TYPE: PROTOCOL_NAME,
        FIELD_CUSTOM_DATA: {"v": 1, "t": MessageType.JOIN, "n": nick},
    }


def _msg_fields(ch, src, nick="user", body="hello"):
    """Build raw LXMF fields dict for a MESSAGE envelope."""
    return {
        FIELD_CHANNEL_HASH: ch,
        FIELD_SOURCE_HASH: src,
        FIELD_CUSTOM_TYPE: PROTOCOL_NAME,
        FIELD_CUSTOM_DATA: {"v": 1, "t": MessageType.MESSAGE, "n": nick, "b": body},
    }


def _mock_lxmf_message(fields):
    """Wrap a fields dict in a mock LXMF message object."""
    msg = MagicMock()
    msg.fields = fields
    return msg


# --- 1. test_hub_init_registers_delivery_identity ---

def test_hub_init_registers_delivery_identity():
    """Verify Hub.__init__ calls router.register_delivery_identity()
    with the identity and 'lxcf-hub' display name, and registers
    the delivery callback.

    Validates: Requirement 1.1
    """
    hub, mock_router, mock_identity, _ = _make_hub()

    mock_router.register_delivery_identity.assert_called_once_with(
        mock_identity, display_name="lxcf-hub",
    )
    mock_router.register_delivery_callback.assert_called_once_with(
        hub._on_lxmf_delivery,
    )


# --- 2. test_hub_destination_hash ---

def test_hub_destination_hash():
    """Verify destination_hash property returns the mock destination's hash."""
    hub, _, _, mock_dest = _make_hub()
    assert hub.destination_hash == mock_dest.hash
    assert hub.destination_hash == b"\x00" * 16


# --- 3. test_hub_malformed_channel_hash_discarded ---

def test_hub_malformed_channel_hash_discarded():
    """Create an envelope with a channel_hash that's not 16 bytes (8 bytes),
    call _on_lxmf_delivery, verify the envelope is discarded.

    Validates: Requirement 3.5
    """
    hub, mock_router, _, _ = _make_hub()

    bad_fields = {
        FIELD_CHANNEL_HASH: b"\xaa" * 8,  # only 8 bytes — malformed
        FIELD_SOURCE_HASH: SRC_1,
        FIELD_CUSTOM_TYPE: PROTOCOL_NAME,
        FIELD_CUSTOM_DATA: {"v": 1, "t": MessageType.JOIN, "n": "user"},
    }
    mock_msg = _mock_lxmf_message(bad_fields)
    hub._on_lxmf_delivery(mock_msg)

    # No subscriptions should have been created
    assert len(hub._subscriptions) == 0


# --- 4. test_hub_malformed_envelope_missing_fields ---

def test_hub_malformed_envelope_missing_fields():
    """Call _on_lxmf_delivery with fields missing FIELD_SOURCE_HASH,
    verify discarded.

    Validates: Requirement 3.5
    """
    hub, _, _, _ = _make_hub()

    bad_fields = {
        FIELD_CHANNEL_HASH: CH_A,
        # FIELD_SOURCE_HASH intentionally missing
        FIELD_CUSTOM_TYPE: PROTOCOL_NAME,
        FIELD_CUSTOM_DATA: {"v": 1, "t": MessageType.JOIN, "n": "user"},
    }
    mock_msg = _mock_lxmf_message(bad_fields)
    hub._on_lxmf_delivery(mock_msg)

    assert len(hub._subscriptions) == 0


# --- 5. test_hub_non_envelope_ignored ---

def test_hub_non_envelope_ignored():
    """Call _on_lxmf_delivery with fields that don't contain
    FIELD_CHANNEL_HASH, verify silently ignored.
    """
    hub, _, _, _ = _make_hub()

    non_envelope_fields = {
        FIELD_CUSTOM_TYPE: PROTOCOL_NAME,
        FIELD_CUSTOM_DATA: {"v": 1, "t": MessageType.MESSAGE, "n": "user"},
    }
    mock_msg = _mock_lxmf_message(non_envelope_fields)
    hub._on_lxmf_delivery(mock_msg)

    assert len(hub._subscriptions) == 0


# --- 6. test_hub_capacity_channel_limit_exactly_at ---

def test_hub_capacity_channel_limit_exactly_at():
    """Create Hub with max_channels=2, JOIN 2 different channels, verify
    both exist. Then JOIN a 3rd — verify it's rejected.

    Validates: Requirements 9.1, 9.4
    """
    hub, _, _, _ = _make_hub(max_channels=2)
    mock_lxmf = MagicMock()

    # JOIN channel A
    env_a = ChannelEnvelope(CH_A, SRC_1, PROTOCOL_NAME,
                            {"v": 1, "t": MessageType.JOIN, "n": "user"})
    hub._handle_envelope(env_a, mock_lxmf)

    # JOIN channel B
    env_b = ChannelEnvelope(CH_B, SRC_1, PROTOCOL_NAME,
                            {"v": 1, "t": MessageType.JOIN, "n": "user"})
    hub._handle_envelope(env_b, mock_lxmf)

    assert len(hub._subscriptions) == 2
    assert CH_A in hub._subscriptions
    assert CH_B in hub._subscriptions

    # JOIN channel C — should be rejected (limit is 2)
    env_c = ChannelEnvelope(CH_C, SRC_1, PROTOCOL_NAME,
                            {"v": 1, "t": MessageType.JOIN, "n": "user"})
    hub._handle_envelope(env_c, mock_lxmf)

    assert len(hub._subscriptions) == 2
    assert CH_C not in hub._subscriptions


# --- 7. test_hub_capacity_subscriber_limit_exactly_at ---

def test_hub_capacity_subscriber_limit_exactly_at():
    """Create Hub with max_subscribers_per_channel=2, JOIN 2 subscribers
    to same channel, verify both exist. Then JOIN a 3rd — verify rejected.

    Validates: Requirements 9.2, 9.5
    """
    hub, _, _, _ = _make_hub(max_subscribers_per_channel=2)
    mock_lxmf = MagicMock()

    # JOIN subscriber 1
    env_1 = ChannelEnvelope(CH_A, SRC_1, PROTOCOL_NAME,
                            {"v": 1, "t": MessageType.JOIN, "n": "user1"})
    hub._handle_envelope(env_1, mock_lxmf)

    # JOIN subscriber 2
    env_2 = ChannelEnvelope(CH_A, SRC_2, PROTOCOL_NAME,
                            {"v": 1, "t": MessageType.JOIN, "n": "user2"})
    hub._handle_envelope(env_2, mock_lxmf)

    assert len(hub._subscriptions[CH_A]) == 2
    assert SRC_1 in hub._subscriptions[CH_A]
    assert SRC_2 in hub._subscriptions[CH_A]

    # JOIN subscriber 3 — should be rejected (limit is 2)
    env_3 = ChannelEnvelope(CH_A, SRC_3, PROTOCOL_NAME,
                            {"v": 1, "t": MessageType.JOIN, "n": "user3"})
    hub._handle_envelope(env_3, mock_lxmf)

    assert len(hub._subscriptions[CH_A]) == 2
    assert SRC_3 not in hub._subscriptions[CH_A]


# --- 8. test_hub_empty_subscriber_discard ---

def test_hub_empty_subscriber_discard():
    """Send a MESSAGE envelope for a channel with no subscribers,
    verify handle_outbound is never called (message silently discarded).

    Validates: Requirement 4.4
    """
    hub, mock_router, _, _ = _make_hub()
    mock_router.handle_outbound.reset_mock()

    # Send a MESSAGE to a channel nobody has joined
    env = ChannelEnvelope(CH_A, SRC_1, PROTOCOL_NAME,
                          {"v": 1, "t": MessageType.MESSAGE, "n": "user", "b": "hello"})
    hub._handle_envelope(env, MagicMock())

    mock_router.handle_outbound.assert_not_called()


# --- 9. test_hub_capacity_configurable ---

def test_hub_capacity_configurable():
    """Create Hub with custom max_channels=5 and max_subscribers_per_channel=10,
    verify the limits are stored correctly.

    Validates: Requirement 9.3
    """
    hub, _, _, _ = _make_hub(max_channels=5, max_subscribers_per_channel=10)
    assert hub._max_channels == 5
    assert hub._max_subscribers_per_channel == 10
