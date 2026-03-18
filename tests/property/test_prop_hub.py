"""
Property tests for Hub subscription management and relay.

Feature: relay-hub-model
- Property 4: JOIN adds and LEAVE removes subscribers
- Property 5: Hub relay fan-out excludes sender
- Property 6: All stanza types relay through Hub

Validates: Requirements 1.3, 3.2, 3.4, 4.2, 8.2, 8.3
"""

import sys
from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from lxcf.envelope import ChannelEnvelope
from lxcf.protocol import MessageType, PROTOCOL_NAME, FIELD_CHANNEL_HASH, FIELD_SOURCE_HASH, FIELD_CUSTOM_DATA
from lxcf.hub import Hub

from tests.conftest import channel_hash, subscriber_set, lxcf_nick, lxcf_message, identity_hash


def _make_hub() -> Hub:
    """Create a Hub with a mock router and identity."""
    mock_dest = MagicMock()
    mock_dest.hash = b"\x00" * 16

    mock_router = MagicMock()
    mock_router.register_delivery_identity.return_value = mock_dest
    mock_router.register_delivery_callback = MagicMock()
    mock_router.handle_outbound = MagicMock()

    mock_identity = MagicMock()

    return Hub(router=mock_router, identity=mock_identity)


def _join_envelope(ch: bytes, src: bytes, nick: str) -> ChannelEnvelope:
    """Build a JOIN ChannelEnvelope."""
    return ChannelEnvelope(
        channel_hash=ch,
        source_hash=src,
        custom_type=PROTOCOL_NAME,
        custom_data={"v": 1, "t": MessageType.JOIN, "n": nick},
    )


def _leave_envelope(ch: bytes, src: bytes, nick: str) -> ChannelEnvelope:
    """Build a LEAVE ChannelEnvelope."""
    return ChannelEnvelope(
        channel_hash=ch,
        source_hash=src,
        custom_type=PROTOCOL_NAME,
        custom_data={"v": 1, "t": MessageType.LEAVE, "n": nick},
    )


# --- Property 4: JOIN adds and LEAVE removes subscribers ---

@given(
    ch=channel_hash(),
    subs=subscriber_set(),
    nick=lxcf_nick(),
)
@settings(max_examples=500)
def test_join_adds_and_leave_removes_subscribers(
    ch: bytes, subs: set[bytes], nick: str,
):
    """
    Feature: relay-hub-model, Property 4: JOIN adds and LEAVE removes subscribers

    For any Channel_Hash and for any sequence of distinct subscriber hashes,
    processing a JOIN envelope for each subscriber shall result in all of them
    appearing in the subscription set, and subsequently processing a LEAVE
    envelope for each shall result in an empty subscription set.

    Validates: Requirements 3.2, 3.4
    """
    hub = _make_hub()
    mock_lxmf = MagicMock()

    # JOIN each subscriber
    for src in subs:
        env = _join_envelope(ch, src, nick)
        hub._handle_envelope(env, mock_lxmf)

    # All subscribers should be present
    assert ch in hub._subscriptions
    assert hub._subscriptions[ch] == subs

    # LEAVE each subscriber
    for src in subs:
        env = _leave_envelope(ch, src, nick)
        hub._handle_envelope(env, mock_lxmf)

    # Subscription set should be empty (channel removed from registry)
    assert ch not in hub._subscriptions


def _message_envelope(ch: bytes, src: bytes, nick: str) -> ChannelEnvelope:
    """Build a MESSAGE ChannelEnvelope."""
    return ChannelEnvelope(
        channel_hash=ch,
        source_hash=src,
        custom_type=PROTOCOL_NAME,
        custom_data={"v": 1, "t": MessageType.MESSAGE, "n": nick, "b": "hello"},
    )


# --- Property 5: Hub relay fan-out excludes sender ---

@given(
    ch=channel_hash(),
    subs=subscriber_set(),
    nick=lxcf_nick(),
)
@settings(max_examples=500)
def test_relay_fan_out_excludes_sender(
    ch: bytes, subs: set[bytes], nick: str,
):
    """
    Feature: relay-hub-model, Property 5: Hub relay fan-out excludes sender

    For any Channel_Hash with N subscribers (N >= 1) and for any message
    sent by one of those subscribers, the Hub shall relay the message to
    exactly N-1 destinations (all subscribers except the sender).

    Validates: Requirements 1.3, 4.2
    """
    hub = _make_hub()
    mock_lxmf = MagicMock()

    # JOIN all subscribers
    for src in subs:
        env = _join_envelope(ch, src, nick)
        hub._handle_envelope(env, mock_lxmf)

    # Pick one subscriber as the sender
    sender = next(iter(subs))
    n = len(subs)

    # Reset handle_outbound call count after JOINs
    hub._router.handle_outbound.reset_mock()

    # Mock RNS and LXMF modules so _relay() can execute its lazy imports
    mock_rns = MagicMock()
    mock_rns.Identity.recall.return_value = MagicMock()  # identity found
    mock_rns.Destination.return_value = MagicMock()
    mock_rns.Destination.OUT = 1
    mock_rns.Destination.SINGLE = 2

    mock_lxmf_mod = MagicMock()
    mock_lxmf_mod.LXMessage.DIRECT = 0
    mock_lxmf_mod.LXMessage.return_value = MagicMock()

    with patch.dict(sys.modules, {"RNS": mock_rns, "LXMF": mock_lxmf_mod}):
        msg_env = _message_envelope(ch, sender, nick)
        hub._handle_envelope(msg_env, mock_lxmf)

    # Should relay to exactly N-1 subscribers (all except sender)
    assert hub._router.handle_outbound.call_count == n - 1

    # Verify none of the outbound destinations match the sender.
    # Each call to handle_outbound receives an LXMessage as first arg.
    # The LXMessage was constructed with a Destination built from
    # RNS.Destination(identity, OUT, SINGLE, "lxmf", "delivery").
    # We check that RNS.Identity.recall was never called with the sender hash.
    recalled_hashes = [
        call.args[0] for call in mock_rns.Identity.recall.call_args_list
    ]
    assert sender not in recalled_hashes


# --- Property 6: All stanza types relay through Hub ---

@given(
    msg=lxcf_message(),
    ch=channel_hash(),
    sender=identity_hash(),
    receiver=identity_hash(),
)
@settings(max_examples=500)
def test_all_stanza_types_relay_through_hub(
    msg, ch: bytes, sender: bytes, receiver: bytes,
):
    """
    Feature: relay-hub-model, Property 6: All stanza types relay through Hub

    For any stanza type in MessageType.ALL and for any valid LXCFMessage of
    that type, wrapping in a ChannelEnvelope and processing through the Hub
    relay path shall produce a relayed envelope containing the same stanza
    type and content.

    Validates: Requirements 8.2, 8.3
    """
    from hypothesis import assume
    assume(sender != receiver)

    hub = _make_hub()
    mock_lxmf = MagicMock()

    # JOIN both sender and receiver
    hub._handle_envelope(_join_envelope(ch, sender, "sender"), mock_lxmf)
    hub._handle_envelope(_join_envelope(ch, receiver, "receiver"), mock_lxmf)

    # Reset after JOINs
    hub._router.handle_outbound.reset_mock()

    # Build the envelope from the message's to_fields() output
    fields = msg.to_fields()
    envelope = ChannelEnvelope(
        channel_hash=ch,
        source_hash=sender,
        custom_type=fields[0xFB],  # FIELD_CUSTOM_TYPE
        custom_data=fields[0xFC],  # FIELD_CUSTOM_DATA
    )

    # Mock RNS and LXMF modules (same pattern as test_relay_fan_out_excludes_sender)
    mock_rns = MagicMock()
    mock_rns.Identity.recall.return_value = MagicMock()
    mock_rns.Destination.return_value = MagicMock()
    mock_rns.Destination.OUT = 1
    mock_rns.Destination.SINGLE = 2

    mock_lxmf_mod = MagicMock()
    mock_lxmf_mod.LXMessage.DIRECT = 0
    mock_lxmf_mod.LXMessage.return_value = MagicMock()

    with patch.dict(sys.modules, {"RNS": mock_rns, "LXMF": mock_lxmf_mod}):
        hub._handle_envelope(envelope, mock_lxmf)

    # Should have relayed to exactly 1 subscriber (receiver, excluding sender)
    assert hub._router.handle_outbound.call_count == 1

    # Capture the fields dict passed to the outbound LXMessage constructor
    call_kwargs = mock_lxmf_mod.LXMessage.call_args_list[-1]
    relayed_fields = call_kwargs.kwargs.get("fields") or call_kwargs[1].get("fields")

    # The relayed fields contain FIELD_CHANNEL_HASH matching the original
    assert relayed_fields[FIELD_CHANNEL_HASH] == ch

    # The relayed fields contain FIELD_SOURCE_HASH matching the sender
    assert relayed_fields[FIELD_SOURCE_HASH] == sender

    # The relayed fields contain FIELD_CUSTOM_DATA matching the original stanza data
    assert relayed_fields[FIELD_CUSTOM_DATA] == fields[0xFC]


# --- Property 10: Hub capacity enforcement ---

@given(
    max_channels=st.integers(min_value=1, max_value=5),
    max_subs=st.integers(min_value=1, max_value=5),
    nick=lxcf_nick(),
    data=st.data(),
)
@settings(max_examples=500)
def test_hub_capacity_enforcement(
    max_channels: int, max_subs: int, nick: str, data,
):
    """
    Feature: relay-hub-model, Property 10: Hub capacity enforcement

    For any Hub configured with max_channels=M and max_subscribers_per_channel=S,
    after M distinct Channel_Hashes have been registered via JOIN, a JOIN for a
    new (M+1)th Channel_Hash shall be discarded. Similarly, after S subscribers
    have joined a single channel, a JOIN from a new (S+1)th subscriber shall be
    discarded.

    Validates: Requirements 9.1, 9.2
    """
    M = max_channels
    S = max_subs

    # Generate M+1 distinct channel hashes
    channel_hashes = data.draw(
        st.lists(
            st.binary(min_size=16, max_size=16),
            min_size=M + 1,
            max_size=M + 1,
            unique=True,
        ),
        label="channel_hashes",
    )

    # Generate S+1 distinct subscriber hashes (for subscriber limit test)
    sub_hashes = data.draw(
        st.lists(
            st.binary(min_size=16, max_size=16),
            min_size=S + 1,
            max_size=S + 1,
            unique=True,
        ),
        label="subscriber_hashes",
    )

    # Create a Hub with the given capacity limits
    mock_dest = MagicMock()
    mock_dest.hash = b"\x00" * 16
    mock_router = MagicMock()
    mock_router.register_delivery_identity.return_value = mock_dest
    mock_router.register_delivery_callback = MagicMock()
    mock_identity = MagicMock()

    hub = Hub(
        router=mock_router,
        identity=mock_identity,
        max_channels=M,
        max_subscribers_per_channel=S,
    )
    mock_lxmf = MagicMock()

    # --- Channel limit ---
    # JOIN one subscriber to each of M channels — all should succeed
    first_sub = sub_hashes[0]
    for i in range(M):
        env = _join_envelope(channel_hashes[i], first_sub, nick)
        hub._handle_envelope(env, mock_lxmf)

    assert len(hub._subscriptions) == M
    for i in range(M):
        assert channel_hashes[i] in hub._subscriptions

    # JOIN on the (M+1)th channel — should be discarded (channel not registered)
    overflow_ch = channel_hashes[M]
    env = _join_envelope(overflow_ch, first_sub, nick)
    hub._handle_envelope(env, mock_lxmf)

    assert len(hub._subscriptions) == M
    assert overflow_ch not in hub._subscriptions

    # --- Subscriber limit ---
    # Pick the first channel and fill it to S subscribers
    target_ch = channel_hashes[0]
    # first_sub is already subscribed from the channel-limit phase above
    for j in range(1, S):
        env = _join_envelope(target_ch, sub_hashes[j], nick)
        hub._handle_envelope(env, mock_lxmf)

    assert len(hub._subscriptions[target_ch]) == S

    # JOIN an (S+1)th subscriber — should be discarded
    overflow_sub = sub_hashes[S]
    env = _join_envelope(target_ch, overflow_sub, nick)
    hub._handle_envelope(env, mock_lxmf)

    assert len(hub._subscriptions[target_ch]) == S
    assert overflow_sub not in hub._subscriptions[target_ch]


# --- Property 13: Hub relays encrypted envelopes without modification ---

@given(
    msg=lxcf_message(),
    ch=channel_hash(),
    sender=identity_hash(),
    receiver=identity_hash(),
    key=st.binary(min_size=32, max_size=32),
)
@settings(max_examples=500)
def test_hub_relays_encrypted_envelopes_without_modification(
    msg, ch: bytes, sender: bytes, receiver: bytes, key: bytes,
):
    """
    Feature: relay-hub-model, Property 13: Hub relays encrypted envelopes without modification

    For any ChannelEnvelope where FIELD_CUSTOM_DATA is encrypted bytes,
    the Hub shall relay the envelope with the FIELD_CUSTOM_DATA value
    byte-identical to the inbound value.

    Validates: Requirements 10.2
    """
    from hypothesis import assume
    from lxcf.envelope import encrypt_custom_data

    assume(sender != receiver)

    hub = _make_hub()
    mock_lxmf = MagicMock()

    # JOIN both sender and receiver
    hub._handle_envelope(_join_envelope(ch, sender, "sender"), mock_lxmf)
    hub._handle_envelope(_join_envelope(ch, receiver, "receiver"), mock_lxmf)
    hub._router.handle_outbound.reset_mock()

    # Encrypt the stanza data
    fields = msg.to_fields()
    ciphertext = encrypt_custom_data(fields[0xFC], key)

    envelope = ChannelEnvelope(
        channel_hash=ch,
        source_hash=sender,
        custom_type=fields[0xFB],
        custom_data=ciphertext,  # encrypted bytes
    )

    mock_rns = MagicMock()
    mock_rns.Identity.recall.return_value = MagicMock()
    mock_rns.Destination.return_value = MagicMock()
    mock_rns.Destination.OUT = 1
    mock_rns.Destination.SINGLE = 2

    mock_lxmf_mod = MagicMock()
    mock_lxmf_mod.LXMessage.DIRECT = 0
    mock_lxmf_mod.LXMessage.return_value = MagicMock()

    with patch.dict(sys.modules, {"RNS": mock_rns, "LXMF": mock_lxmf_mod}):
        hub._handle_envelope(envelope, mock_lxmf)

    assert hub._router.handle_outbound.call_count == 1

    call_kwargs = mock_lxmf_mod.LXMessage.call_args_list[-1]
    relayed_fields = call_kwargs.kwargs.get("fields") or call_kwargs[1].get("fields")

    # FIELD_CUSTOM_DATA must be byte-identical to the inbound ciphertext
    assert relayed_fields[FIELD_CUSTOM_DATA] == ciphertext
    assert isinstance(relayed_fields[FIELD_CUSTOM_DATA], bytes)

    # Routing fields preserved
    assert relayed_fields[FIELD_CHANNEL_HASH] == ch
    assert relayed_fields[FIELD_SOURCE_HASH] == sender
