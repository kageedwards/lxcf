"""
Property tests for Client hub-aware routing.

Feature: relay-hub-model
- Property 7: Client routes to correct hub per channel
- Property 8: Inbound dispatch by Channel_Hash
- Property 9: No self-echo on relayed messages

Validates: Requirements 6.2, 6.3, 7.1, 7.3
"""

from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from lxcf.client import Client
from lxcf.envelope import ChannelEnvelope
from lxcf.message import LXCFMessage
from lxcf.protocol import FIELD_CUSTOM_TYPE, FIELD_CUSTOM_DATA, PROTOCOL_NAME, derive_channel_hash

from tests.conftest import lxcf_channel, lxcf_nick, identity_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hub_client(nick: str = "tester") -> Client:
    """
    Create a Client with a mock router so hub-aware paths are reachable
    but no real RNS/LXMF is needed.
    """
    mock_dest = MagicMock()
    mock_dest.hash = b"\xAA" * 16

    mock_router = MagicMock()
    mock_router.register_delivery_identity.return_value = mock_dest
    mock_router.register_delivery_callback = MagicMock()

    client = Client(router=mock_router, destination=mock_dest, nick=nick)
    return client


# ---------------------------------------------------------------------------
# Property 7: Client routes to correct hub per channel
# ---------------------------------------------------------------------------

@given(
    data=st.data(),
    nick=lxcf_nick(),
)
@settings(max_examples=500)
def test_client_routes_to_correct_hub_per_channel(data, nick: str):
    """
    Feature: relay-hub-model, Property 7: Client routes to correct hub per channel

    For any set of channels each associated with a distinct hub hash, when the
    Client sends a message to a given channel, the outbound LXMF message shall
    be addressed to the hub hash associated with that channel.

    Validates: Requirements 6.2, 6.3
    """
    # Generate 1-5 distinct channel names
    channels = data.draw(
        st.lists(lxcf_channel(), min_size=1, max_size=5, unique=True),
        label="channels",
    )
    # Generate matching distinct hub hashes (16 bytes each)
    hub_hashes = data.draw(
        st.lists(
            st.binary(min_size=16, max_size=16),
            min_size=len(channels),
            max_size=len(channels),
            unique=True,
        ),
        label="hub_hashes",
    )

    client = _make_hub_client(nick)

    # Patch _make_group_destination to avoid RNS imports
    with patch.object(client, "_make_group_destination", return_value=None):
        # Patch _send_to_hub to capture calls without triggering RNS/LXMF
        send_log: list[tuple[bytes, bytes]] = []  # (channel_hash, hub_hash)
        original_send_to_hub = Client._send_to_hub

        def mock_send_to_hub(self, channel, msg):
            send_log.append((channel.channel_hash, channel.hub_hash))

        with patch.object(Client, "_send_to_hub", mock_send_to_hub):
            # Join each channel with its corresponding hub hash
            joined = []
            for ch_name, hub_hash in zip(channels, hub_hashes):
                ch = client.join(ch_name, hub=hub_hash, announce=False)
                joined.append((ch, hub_hash))

            # Send a message to each channel and verify routing
            for ch, expected_hub in joined:
                send_log.clear()
                ch.send("test message")

                # Exactly one call to _send_to_hub
                assert len(send_log) == 1, (
                    f"Expected 1 _send_to_hub call for {ch.name}, got {len(send_log)}"
                )

                actual_ch_hash, actual_hub_hash = send_log[0]

                # The hub hash must match the one associated at join time
                assert actual_hub_hash == expected_hub, (
                    f"Channel {ch.name}: expected hub {expected_hub.hex()}, "
                    f"got {actual_hub_hash.hex()}"
                )

                # The channel hash must match the derived hash
                expected_ch_hash = derive_channel_hash(ch.name)
                assert actual_ch_hash == expected_ch_hash, (
                    f"Channel {ch.name}: expected ch_hash {expected_ch_hash.hex()}, "
                    f"got {actual_ch_hash.hex()}"
                )


# ---------------------------------------------------------------------------
# Property 8: Inbound dispatch by Channel_Hash
# ---------------------------------------------------------------------------

@given(
    channel_name=lxcf_channel(),
    nick=lxcf_nick(),
    sender_hash=identity_hash(),
    hub_hash=identity_hash(),
)
@settings(max_examples=500)
def test_inbound_dispatch_by_channel_hash(channel_name: str, nick: str, sender_hash: bytes, hub_hash: bytes):
    """
    Feature: relay-hub-model, Property 8: Inbound dispatch by Channel_Hash

    For any Channel_Envelope received by a Client, if the enclosed
    Channel_Hash matches a locally joined channel, the inner LXCFMessage
    shall be dispatched to that channel. If the Channel_Hash does not match
    any joined channel, the message shall be discarded.

    Validates: Requirements 7.1
    """
    client_hash = b"\xAA" * 16

    # Ensure sender_hash differs from client hash to avoid self-echo suppression
    assume(sender_hash != client_hash)

    client = _make_hub_client(nick)

    with patch.object(client, "_make_group_destination", return_value=None):
        ch = client.join(channel_name, hub=hub_hash, announce=False)

    # --- Matching case: envelope channel_hash matches the joined channel ---
    expected_ch_hash = derive_channel_hash(channel_name)
    assert ch.channel_hash == expected_ch_hash

    inner_msg = LXCFMessage.chat(nick="remote_user", channel=channel_name, body="hello")
    inner_fields = inner_msg.to_fields()

    envelope = ChannelEnvelope(
        channel_hash=expected_ch_hash,
        source_hash=sender_hash,
        custom_type=inner_fields[FIELD_CUSTOM_TYPE],
        custom_data=inner_fields[FIELD_CUSTOM_DATA],
    )

    mock_lxmf_msg = MagicMock()
    mock_lxmf_msg.fields = envelope.to_fields()

    history_before = len(ch.history)
    client._on_lxmf_delivery(mock_lxmf_msg)

    # The message should have been dispatched to the channel
    assert len(ch.history) == history_before + 1
    dispatched = ch.history[-1]
    assert dispatched.body == "hello"
    assert dispatched.nick == "remote_user"

    # --- Discard case: envelope with non-matching channel_hash ---
    unmatched_hash = b"\xBB" * 16
    # Make sure it doesn't accidentally match
    assume(unmatched_hash != expected_ch_hash)

    discard_envelope = ChannelEnvelope(
        channel_hash=unmatched_hash,
        source_hash=sender_hash,
        custom_type=inner_fields[FIELD_CUSTOM_TYPE],
        custom_data=inner_fields[FIELD_CUSTOM_DATA],
    )

    mock_lxmf_msg_discard = MagicMock()
    mock_lxmf_msg_discard.fields = discard_envelope.to_fields()

    history_before_discard = len(ch.history)
    client._on_lxmf_delivery(mock_lxmf_msg_discard)

    # The message should NOT have been dispatched
    assert len(ch.history) == history_before_discard


# ---------------------------------------------------------------------------
# Property 9: No self-echo on relayed messages
# ---------------------------------------------------------------------------

@given(
    channel_name=lxcf_channel(),
    nick=lxcf_nick(),
    hub_hash=identity_hash(),
)
@settings(max_examples=500)
def test_no_self_echo_on_relayed_messages(channel_name: str, nick: str, hub_hash: bytes):
    """
    Feature: relay-hub-model, Property 9: No self-echo on relayed messages

    For any Channel_Envelope where the `src` field equals the receiving
    Client's own destination hash, the Client shall not dispatch the
    message to the local channel.

    Validates: Requirements 7.3
    """
    client_hash = b"\xAA" * 16

    client = _make_hub_client(nick)

    with patch.object(client, "_make_group_destination", return_value=None):
        ch = client.join(channel_name, hub=hub_hash, announce=False)

    expected_ch_hash = derive_channel_hash(channel_name)
    assert ch.channel_hash == expected_ch_hash

    # Build an envelope where source_hash == client's own destination hash
    inner_msg = LXCFMessage.chat(nick="remote_user", channel=channel_name, body="echo test")
    inner_fields = inner_msg.to_fields()

    envelope = ChannelEnvelope(
        channel_hash=expected_ch_hash,
        source_hash=client_hash,  # same as client._destination.hash
        custom_type=inner_fields[FIELD_CUSTOM_TYPE],
        custom_data=inner_fields[FIELD_CUSTOM_DATA],
    )

    mock_lxmf_msg = MagicMock()
    mock_lxmf_msg.fields = envelope.to_fields()

    history_before = len(ch.history)
    client._on_lxmf_delivery(mock_lxmf_msg)

    # The message should NOT have been dispatched (self-echo suppressed)
    assert len(ch.history) == history_before, (
        f"Self-echo was not suppressed: history grew from {history_before} "
        f"to {len(ch.history)} for channel {channel_name}"
    )
