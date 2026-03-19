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
from lxcf.protocol import FIELD_CUSTOM_TYPE, FIELD_CUSTOM_DATA, derive_channel_hash

from tests.conftest import lxcf_channel, lxcf_nick, identity_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hub_client(nick: str = "tester") -> Client:
    """Create a Client with a mock router for hub-aware testing."""
    mock_dest = MagicMock()
    mock_dest.hash = b"\xAA" * 16
    mock_router = MagicMock()
    mock_router.register_delivery_callback = MagicMock()
    return Client(router=mock_router, destination=mock_dest, nick=nick)


# ---------------------------------------------------------------------------
# Property 7: Client routes to correct hub per channel
# ---------------------------------------------------------------------------

@given(data=st.data(), nick=lxcf_nick())
@settings(max_examples=500)
def test_client_routes_to_correct_hub_per_channel(data, nick: str):
    """
    Feature: relay-hub-model, Property 7

    For any set of channels each associated with a distinct hub hash,
    sending a message routes to the correct hub.

    Validates: Requirements 6.2, 6.3
    """
    channels = data.draw(
        st.lists(lxcf_channel(), min_size=1, max_size=5, unique=True),
        label="channels",
    )
    hub_hashes = data.draw(
        st.lists(
            st.binary(min_size=16, max_size=16),
            min_size=len(channels), max_size=len(channels), unique=True,
        ),
        label="hub_hashes",
    )

    client = _make_hub_client(nick)
    send_log: list[tuple[bytes, bytes]] = []

    def mock_send_to_hub(self, channel, msg):
        send_log.append((channel.channel_hash, channel.hub_hash))

    with patch.object(Client, "_send_to_hub", mock_send_to_hub):
        joined = []
        for ch_name, hub_hash in zip(channels, hub_hashes):
            ch = client.join(ch_name, hub=hub_hash, announce=False)
            joined.append((ch, hub_hash))

        for ch, expected_hub in joined:
            send_log.clear()
            ch.send("test message")

            assert len(send_log) == 1
            actual_ch_hash, actual_hub_hash = send_log[0]
            assert actual_hub_hash == expected_hub
            assert actual_ch_hash == derive_channel_hash(ch.name)


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
def test_inbound_dispatch_by_channel_hash(channel_name, nick, sender_hash, hub_hash):
    """
    Feature: relay-hub-model, Property 8

    Inbound Channel Envelope with matching channel_hash dispatches;
    non-matching is discarded.

    Validates: Requirements 7.1
    """
    client_hash = b"\xAA" * 16
    assume(sender_hash != client_hash)

    client = _make_hub_client(nick)
    ch = client.join(channel_name, hub=hub_hash, announce=False)

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
    assert len(ch.history) == history_before + 1
    assert ch.history[-1].body == "hello"

    # Discard case
    unmatched_hash = b"\xBB" * 16
    assume(unmatched_hash != expected_ch_hash)

    discard_envelope = ChannelEnvelope(
        channel_hash=unmatched_hash,
        source_hash=sender_hash,
        custom_type=inner_fields[FIELD_CUSTOM_TYPE],
        custom_data=inner_fields[FIELD_CUSTOM_DATA],
    )
    mock_discard = MagicMock()
    mock_discard.fields = discard_envelope.to_fields()

    history_before = len(ch.history)
    client._on_lxmf_delivery(mock_discard)
    assert len(ch.history) == history_before


# ---------------------------------------------------------------------------
# Property 9: No self-echo on relayed messages
# ---------------------------------------------------------------------------

@given(
    channel_name=lxcf_channel(),
    nick=lxcf_nick(),
    hub_hash=identity_hash(),
)
@settings(max_examples=500)
def test_no_self_echo_on_relayed_messages(channel_name, nick, hub_hash):
    """
    Feature: relay-hub-model, Property 9

    Envelope with source_hash == own hash is suppressed.

    Validates: Requirements 7.3
    """
    client_hash = b"\xAA" * 16
    client = _make_hub_client(nick)
    ch = client.join(channel_name, hub=hub_hash, announce=False)

    expected_ch_hash = derive_channel_hash(channel_name)
    inner_msg = LXCFMessage.chat(nick="remote_user", channel=channel_name, body="echo test")
    inner_fields = inner_msg.to_fields()

    envelope = ChannelEnvelope(
        channel_hash=expected_ch_hash,
        source_hash=client_hash,
        custom_type=inner_fields[FIELD_CUSTOM_TYPE],
        custom_data=inner_fields[FIELD_CUSTOM_DATA],
    )
    mock_msg = MagicMock()
    mock_msg.fields = envelope.to_fields()

    history_before = len(ch.history)
    client._on_lxmf_delivery(mock_msg)
    assert len(ch.history) == history_before
