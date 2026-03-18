"""
Property tests for Channel Envelope.

Feature: relay-hub-model, Property 2: Channel Envelope round-trip
Feature: relay-hub-model, Property 3: Envelope structural validity

Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from lxcf.protocol import (
    FIELD_CHANNEL_HASH,
    FIELD_SOURCE_HASH,
    FIELD_CUSTOM_TYPE,
    FIELD_CUSTOM_DATA,
    PROTOCOL_NAME,
)
from lxcf.envelope import ChannelEnvelope

from tests.conftest import channel_envelope


# --- Property 2: Channel Envelope round-trip ---

@given(env=channel_envelope())
@settings(max_examples=500)
def test_envelope_round_trip(env: ChannelEnvelope):
    """
    Feature: relay-hub-model, Property 2: Channel Envelope round-trip

    For any valid LXCFMessage, wrapping in a ChannelEnvelope via to_fields()
    and reconstructing via from_fields() followed by unwrap() produces an
    LXCFMessage with identical type, nick, channel, body, thread, ref, and
    extra fields.
    """
    fields = env.to_fields()
    restored = ChannelEnvelope.from_fields(fields)
    original_msg = env.unwrap()
    restored_msg = restored.unwrap()

    assert restored_msg.type == original_msg.type
    assert restored_msg.nick == original_msg.nick
    assert restored_msg.channel == original_msg.channel
    assert restored_msg.body == original_msg.body
    assert restored_msg.thread == original_msg.thread
    assert restored_msg.ref == original_msg.ref
    assert restored_msg.extra == original_msg.extra

    # Envelope routing fields also preserved
    assert restored.channel_hash == env.channel_hash
    assert restored.source_hash == env.source_hash


# --- Property 3: Envelope structural validity ---

@given(env=channel_envelope())
@settings(max_examples=500)
def test_envelope_structural_validity(env: ChannelEnvelope):
    """
    Feature: relay-hub-model, Property 3: Envelope structural validity

    For any ChannelEnvelope constructed with a 16-byte channel_hash, a
    16-byte source_hash, and a valid LXCF stanza, to_fields() produces a
    dict containing FIELD_CHANNEL_HASH as bytes of length 16,
    FIELD_SOURCE_HASH as bytes of length 16, FIELD_CUSTOM_TYPE equal to
    "LXCF", and FIELD_CUSTOM_DATA as a dict.
    """
    fields = env.to_fields()

    ch = fields[FIELD_CHANNEL_HASH]
    assert isinstance(ch, bytes) and len(ch) == 16

    src = fields[FIELD_SOURCE_HASH]
    assert isinstance(src, bytes) and len(src) == 16

    assert fields[FIELD_CUSTOM_TYPE] == PROTOCOL_NAME
    assert isinstance(fields[FIELD_CUSTOM_DATA], dict)
