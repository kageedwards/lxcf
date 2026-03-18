"""
Property tests for private channel encryption.

Feature: relay-hub-model
- Property 11: Private channel encryption preserves cleartext routing
- Property 12: Private channel encrypt/decrypt round-trip

Validates: Requirements 10.1, 10.3
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from lxcf.envelope import ChannelEnvelope, encrypt_custom_data, decrypt_custom_data
from lxcf.protocol import (
    FIELD_CHANNEL_HASH,
    FIELD_SOURCE_HASH,
    FIELD_CUSTOM_DATA,
    FIELD_CUSTOM_TYPE,
    PROTOCOL_NAME,
)

from tests.conftest import lxcf_message, channel_hash, identity_hash


# Fernet requires exactly 32 bytes for the key (before base64 encoding)
symmetric_key = st.binary(min_size=32, max_size=32)


# --- Property 11: Private channel encryption preserves cleartext routing ---

@given(
    msg=lxcf_message(),
    ch=channel_hash(),
    src=identity_hash(),
    key=symmetric_key,
)
@settings(max_examples=500)
def test_encryption_preserves_cleartext_routing(msg, ch: bytes, src: bytes, key: bytes):
    """
    Feature: relay-hub-model, Property 11: Private channel encryption preserves cleartext routing

    For any LXCFMessage and for any symmetric key, encrypting the
    FIELD_CUSTOM_DATA within a ChannelEnvelope shall leave the ch
    (Channel_Hash) and src (source_hash) fields as cleartext bytes,
    while the FIELD_CUSTOM_DATA value shall be of type bytes (ciphertext)
    rather than dict.

    Validates: Requirements 10.1
    """
    fields = msg.to_fields()
    ciphertext = encrypt_custom_data(fields[FIELD_CUSTOM_DATA], key)

    envelope = ChannelEnvelope(
        channel_hash=ch,
        source_hash=src,
        custom_type=fields[FIELD_CUSTOM_TYPE],
        custom_data=ciphertext,
    )

    wire = envelope.to_fields()

    # Routing fields remain cleartext bytes of correct length
    assert wire[FIELD_CHANNEL_HASH] == ch
    assert isinstance(wire[FIELD_CHANNEL_HASH], bytes) and len(wire[FIELD_CHANNEL_HASH]) == 16

    assert wire[FIELD_SOURCE_HASH] == src
    assert isinstance(wire[FIELD_SOURCE_HASH], bytes) and len(wire[FIELD_SOURCE_HASH]) == 16

    # FIELD_CUSTOM_DATA is ciphertext bytes, not a dict
    assert isinstance(wire[FIELD_CUSTOM_DATA], bytes)

    # FIELD_CUSTOM_TYPE is still the protocol name string
    assert wire[FIELD_CUSTOM_TYPE] == PROTOCOL_NAME


# --- Property 12: Private channel encrypt/decrypt round-trip ---

@given(
    msg=lxcf_message(),
    key=symmetric_key,
)
@settings(max_examples=500)
def test_encrypt_decrypt_round_trip(msg, key: bytes):
    """
    Feature: relay-hub-model, Property 12: Private channel encrypt/decrypt round-trip

    For any valid LXCFMessage and for any 32-byte symmetric key,
    encrypting the stanza's FIELD_CUSTOM_DATA with that key and then
    decrypting with the same key shall produce the original stanza dict.

    Validates: Requirements 10.3
    """
    fields = msg.to_fields()
    original_data = fields[FIELD_CUSTOM_DATA]

    ciphertext = encrypt_custom_data(original_data, key)
    decrypted = decrypt_custom_data(ciphertext, key)

    assert decrypted == original_data
