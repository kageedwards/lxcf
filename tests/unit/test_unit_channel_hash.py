"""
Unit tests for Channel_Hash derivation.

Validates: Requirements 2.1, 2.2, 2.3, 2.4
"""

import hashlib

from lxcf.protocol import derive_channel_hash


def test_known_channel_produces_expected_hash():
    """A known channel name produces the expected SHA-256 truncated hash."""
    name = "#general"
    expected = hashlib.sha256(name.encode("utf-8")).digest()[:16]
    assert derive_channel_hash(name) == expected


def test_keyed_channel_differs_from_open():
    """A keyed channel produces a different hash than the same name without a key."""
    name = "#secret"
    key = b"mysecretkey"
    open_hash = derive_channel_hash(name)
    keyed_hash = derive_channel_hash(name, key)
    assert open_hash != keyed_hash


def test_different_keys_produce_different_hashes():
    """Different keys for the same channel name produce different hashes."""
    name = "#private"
    hash_a = derive_channel_hash(name, b"key_alpha")
    hash_b = derive_channel_hash(name, b"key_beta")
    assert hash_a != hash_b


def test_determinism_with_same_inputs():
    """Same name and key always produce the same hash."""
    name = "#mesh"
    key = b"shared"
    assert derive_channel_hash(name, key) == derive_channel_hash(name, key)


def test_hash_length_is_16_bytes():
    """Output is always exactly 16 bytes."""
    assert len(derive_channel_hash("#test")) == 16
    assert len(derive_channel_hash("#test", b"key")) == 16


def test_none_key_same_as_no_key():
    """Passing key=None is equivalent to omitting the key."""
    name = "#open"
    assert derive_channel_hash(name, None) == derive_channel_hash(name)
