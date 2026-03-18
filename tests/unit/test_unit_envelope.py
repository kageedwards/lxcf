"""
Unit tests for ChannelEnvelope.

Validates: Requirements 5.1, 5.2, 5.3, 5.4
"""

import pytest

from lxcf.protocol import (
    FIELD_CHANNEL_HASH,
    FIELD_SOURCE_HASH,
    FIELD_CUSTOM_TYPE,
    FIELD_CUSTOM_DATA,
    PROTOCOL_NAME,
)
from lxcf.message import LXCFMessage
from lxcf.envelope import ChannelEnvelope


CH = b"\x01" * 16
SRC = b"\x02" * 16
STANZA = {"v": 1, "t": "message", "n": "alice", "c": "#test", "b": "hello"}


def _make_envelope(ch=CH, src=SRC, ctype=PROTOCOL_NAME, cdata=None):
    return ChannelEnvelope(ch, src, ctype, cdata or dict(STANZA))


# --- Construction with known values ---

def test_construction_stores_attributes():
    env = _make_envelope()
    assert env.channel_hash == CH
    assert env.source_hash == SRC
    assert env.custom_type == PROTOCOL_NAME
    assert env.custom_data == STANZA


def test_to_fields_contains_all_keys():
    fields = _make_envelope().to_fields()
    assert fields[FIELD_CHANNEL_HASH] == CH
    assert fields[FIELD_SOURCE_HASH] == SRC
    assert fields[FIELD_CUSTOM_TYPE] == PROTOCOL_NAME
    assert fields[FIELD_CUSTOM_DATA] == STANZA


def test_from_fields_reconstructs_envelope():
    fields = _make_envelope().to_fields()
    restored = ChannelEnvelope.from_fields(fields)
    assert restored.channel_hash == CH
    assert restored.source_hash == SRC
    assert restored.custom_type == PROTOCOL_NAME
    assert restored.custom_data == STANZA


# --- unwrap extracts inner LXCFMessage ---

def test_unwrap_produces_correct_message():
    env = _make_envelope()
    msg = env.unwrap()
    assert msg.type == "message"
    assert msg.nick == "alice"
    assert msg.channel == "#test"
    assert msg.body == "hello"


def test_unwrap_encrypted_raises():
    env = ChannelEnvelope(CH, SRC, PROTOCOL_NAME, b"ciphertext")
    with pytest.raises(ValueError, match="decrypt"):
        env.unwrap()


# --- is_envelope detection ---

def test_is_envelope_true_when_channel_hash_present():
    fields = _make_envelope().to_fields()
    assert ChannelEnvelope.is_envelope(fields) is True


def test_is_envelope_false_when_no_channel_hash():
    fields = {FIELD_CUSTOM_TYPE: PROTOCOL_NAME, FIELD_CUSTOM_DATA: STANZA}
    assert ChannelEnvelope.is_envelope(fields) is False


def test_is_envelope_false_on_empty_dict():
    assert ChannelEnvelope.is_envelope({}) is False


# --- Malformed hash rejection ---

def test_from_fields_rejects_short_channel_hash():
    fields = _make_envelope().to_fields()
    fields[FIELD_CHANNEL_HASH] = b"\x01" * 8  # too short
    with pytest.raises(ValueError, match="FIELD_CHANNEL_HASH"):
        ChannelEnvelope.from_fields(fields)


def test_from_fields_rejects_long_channel_hash():
    fields = _make_envelope().to_fields()
    fields[FIELD_CHANNEL_HASH] = b"\x01" * 32  # too long
    with pytest.raises(ValueError, match="FIELD_CHANNEL_HASH"):
        ChannelEnvelope.from_fields(fields)


def test_from_fields_rejects_non_bytes_channel_hash():
    fields = _make_envelope().to_fields()
    fields[FIELD_CHANNEL_HASH] = "not bytes"
    with pytest.raises(ValueError, match="FIELD_CHANNEL_HASH"):
        ChannelEnvelope.from_fields(fields)


def test_from_fields_rejects_short_source_hash():
    fields = _make_envelope().to_fields()
    fields[FIELD_SOURCE_HASH] = b"\x02" * 4
    with pytest.raises(ValueError, match="FIELD_SOURCE_HASH"):
        ChannelEnvelope.from_fields(fields)


def test_from_fields_rejects_missing_custom_type():
    fields = _make_envelope().to_fields()
    del fields[FIELD_CUSTOM_TYPE]
    with pytest.raises(ValueError, match="FIELD_CUSTOM_TYPE"):
        ChannelEnvelope.from_fields(fields)


def test_from_fields_rejects_missing_custom_data():
    fields = _make_envelope().to_fields()
    del fields[FIELD_CUSTOM_DATA]
    with pytest.raises(ValueError, match="FIELD_CUSTOM_DATA"):
        ChannelEnvelope.from_fields(fields)


# --- Private channel encryption ---

from lxcf.envelope import encrypt_custom_data, decrypt_custom_data
from cryptography.fernet import InvalidToken


KEY = b"\x42" * 32
WRONG_KEY = b"\x99" * 32


def test_encrypt_decrypt_with_known_key():
    ct = encrypt_custom_data(STANZA, KEY)
    assert isinstance(ct, bytes)
    result = decrypt_custom_data(ct, KEY)
    assert result == STANZA


def test_wrong_key_raises():
    ct = encrypt_custom_data(STANZA, KEY)
    with pytest.raises(InvalidToken):
        decrypt_custom_data(ct, WRONG_KEY)


def test_encrypted_envelope_has_bytes_custom_data():
    ct = encrypt_custom_data(STANZA, KEY)
    env = ChannelEnvelope(CH, SRC, PROTOCOL_NAME, ct)
    fields = env.to_fields()
    assert isinstance(fields[FIELD_CUSTOM_DATA], bytes)
    # Routing fields remain cleartext
    assert fields[FIELD_CHANNEL_HASH] == CH
    assert fields[FIELD_SOURCE_HASH] == SRC


def test_encrypted_envelope_unwrap_raises():
    """unwrap() should refuse to unwrap encrypted (bytes) custom_data."""
    ct = encrypt_custom_data(STANZA, KEY)
    env = ChannelEnvelope(CH, SRC, PROTOCOL_NAME, ct)
    with pytest.raises(ValueError, match="decrypt"):
        env.unwrap()


def test_encrypted_envelope_round_trip_via_fields():
    """Serialize encrypted envelope to fields and back, then decrypt."""
    ct = encrypt_custom_data(STANZA, KEY)
    env = ChannelEnvelope(CH, SRC, PROTOCOL_NAME, ct)
    fields = env.to_fields()
    restored = ChannelEnvelope.from_fields(fields)
    assert isinstance(restored.custom_data, bytes)
    result = decrypt_custom_data(restored.custom_data, KEY)
    assert result == STANZA
