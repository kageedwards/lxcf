"""
Property tests for channel_id and nick_with_hash.

Properties validated:
  8.  channel_id Open Channel Identity
  9.  channel_id Keyed Channel Encoding
  12. nick_with_hash Format

Validates Requirements: 6.3, 6.4, 6.5, 7.1
"""

import hashlib

from hypothesis import given, settings
from hypothesis import strategies as st

from lxcf.client import channel_id
from lxcf.util import nick_with_hash

from tests.conftest import lxcf_channel, lxcf_nick, identity_hash


# --- Property 8: channel_id Open Channel Identity ---

@given(
    name=lxcf_channel(),
    key=st.one_of(st.none(), st.binary(min_size=1, max_size=32)),
)
@settings(max_examples=200)
def test_channel_id_deterministic(name: str, key: bytes | None):
    """
    Same inputs always produce the same channel_id.

    **Validates: Requirements 6.3, 6.4**
    """
    assert channel_id(name, key) == channel_id(name, key)


@given(name=lxcf_channel())
@settings(max_examples=200)
def test_channel_id_none_key_returns_name(name: str):
    """
    When key is None, channel_id returns the channel name unchanged.

    **Validates: Requirements 6.4**
    """
    assert channel_id(name, None) == name


# --- Property 9: channel_id Keyed Channel Encoding ---

@given(
    name=lxcf_channel(),
    key=st.binary(min_size=1, max_size=32),
)
@settings(max_examples=200)
def test_channel_id_keyed_contains_at_hash(name: str, key: bytes):
    """
    Non-None key produces name@XXXXXXXX where XXXXXXXX is 8 hex chars of SHA-256.

    **Validates: Requirements 6.5**
    """
    result = channel_id(name, key)
    parts = result.split("@")
    assert len(parts) == 2
    assert len(parts[1]) == 8
    int(parts[1], 16)  # valid hex
    assert parts[1] == hashlib.sha256(key).hexdigest()[:8]


# --- Property 12: nick_with_hash Format ---

@given(nick=lxcf_nick(), hash_bytes=identity_hash())
@settings(max_examples=200)
def test_nick_with_hash_format(nick: str, hash_bytes: bytes):
    """
    Output matches f"{nick}~{hash_bytes[:2].hex()}" with exactly one "~".

    **Validates: Requirements 7.1**
    """
    result = nick_with_hash(nick, hash_bytes)
    expected = f"{nick}~{hash_bytes[:2].hex()}"
    assert result == expected
    assert result.count("~") == 1
