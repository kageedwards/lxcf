"""
Property tests for channel_id and nick_with_hash.

Properties validated:
  8.  channel_id determinism
  9.  channel_id hub-based encoding
  12. nick_with_hash Format

Validates Requirements: 6.3, 6.4, 6.5, 7.1
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from lxcf.client import channel_id
from lxcf.util import nick_with_hash

from tests.conftest import lxcf_channel, lxcf_nick, identity_hash


# --- Property 8: channel_id determinism ---

@given(
    name=lxcf_channel(),
    hub=st.one_of(st.none(), st.binary(min_size=16, max_size=16)),
)
@settings(max_examples=200)
def test_channel_id_deterministic(name: str, hub: bytes | None):
    """Same inputs always produce the same channel_id."""
    assert channel_id(name, hub) == channel_id(name, hub)


@given(name=lxcf_channel())
@settings(max_examples=200)
def test_channel_id_none_hub_returns_name(name: str):
    """When hub is None, channel_id returns the channel name unchanged."""
    assert channel_id(name, None) == name


# --- Property 9: channel_id hub-based encoding ---

@given(
    name=lxcf_channel(),
    hub=st.binary(min_size=16, max_size=16),
)
@settings(max_examples=200)
def test_channel_id_hub_contains_at_hash(name: str, hub: bytes):
    """Non-None hub produces name@XXXXXXXX where XXXXXXXX is first 8 hex chars of hub hash."""
    result = channel_id(name, hub)
    parts = result.split("@")
    assert len(parts) == 2
    assert len(parts[1]) == 8
    int(parts[1], 16)  # valid hex
    assert parts[1] == hub.hex()[:8]


# --- Property 12: nick_with_hash Format ---

@given(nick=lxcf_nick(), hash_bytes=identity_hash())
@settings(max_examples=200)
def test_nick_with_hash_format(nick: str, hash_bytes: bytes):
    """Output matches f"{nick}~{hash_bytes[:2].hex()}" with exactly one "~"."""
    result = nick_with_hash(nick, hash_bytes)
    expected = f"{nick}~{hash_bytes[:2].hex()}"
    assert result == expected
    assert result.count("~") == 1
