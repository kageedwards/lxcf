"""
Property tests for Channel_Hash derivation.

Feature: relay-hub-model, Property 1: Channel_Hash derivation correctness

Validates: Requirements 2.1, 2.2, 2.4
"""

import hashlib

from hypothesis import given, settings
from hypothesis import strategies as st

from lxcf.protocol import derive_channel_hash


# Strategy: non-empty UTF-8 channel names
channel_names = st.text(min_size=1, max_size=100)
# Strategy: optional symmetric keys
optional_keys = st.one_of(st.none(), st.binary(min_size=1, max_size=64))


@given(name=channel_names, key=optional_keys)
@settings(max_examples=500)
def test_channel_hash_derivation_correctness(name: str, key: bytes | None):
    """
    Feature: relay-hub-model, Property 1: Channel_Hash derivation correctness

    For any channel name and optional key, derive_channel_hash() produces
    exactly 16 bytes equal to SHA-256(name.encode("utf-8") + (key or b""))[:16].
    """
    result = derive_channel_hash(name, key)
    expected = hashlib.sha256(name.encode("utf-8") + (key or b"")).digest()[:16]

    assert isinstance(result, bytes)
    assert len(result) == 16
    assert result == expected


@given(name=channel_names, key=optional_keys)
@settings(max_examples=200)
def test_channel_hash_determinism(name: str, key: bytes | None):
    """Calling derive_channel_hash twice with the same inputs produces identical output."""
    assert derive_channel_hash(name, key) == derive_channel_hash(name, key)
