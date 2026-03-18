"""
Shared Hypothesis strategies, pytest fixtures, and mock objects
for the lxcf test suite.
"""

import string
import pytest
from hypothesis import settings, HealthCheck
from hypothesis import strategies as st

from lxcf.protocol import (
    FIELD_CUSTOM_TYPE,
    FIELD_CUSTOM_DATA,
    PROTOCOL_NAME,
    MessageType,
)
from lxcf.message import LXCFMessage
from lxcf.envelope import ChannelEnvelope
from lxcf.client import Client

# ---------------------------------------------------------------------------
# Hypothesis settings profile
# ---------------------------------------------------------------------------
settings.register_profile("default", max_examples=200, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("default")

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

@st.composite
def lxcf_nick(draw) -> str:
    """Generate a valid IRC-style nick: 1-20 printable chars, no whitespace."""
    return draw(st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N", "P"),
            blacklist_characters="\t\n\r ~",
        ),
        min_size=1,
        max_size=20,
    ))


@st.composite
def lxcf_channel(draw) -> str:
    """Generate a channel name: '#' + 1-30 alphanumeric chars."""
    name = draw(st.text(
        alphabet=string.ascii_lowercase + string.digits,
        min_size=1,
        max_size=30,
    ))
    return f"#{name}"


@st.composite
def lxcf_message(draw) -> LXCFMessage:
    """Generate an arbitrary valid LXCFMessage with all field combinations."""
    msg_type = draw(st.sampled_from(sorted(MessageType.ALL)))
    nick = draw(lxcf_nick())
    channel = draw(st.one_of(st.none(), lxcf_channel()))
    body = draw(st.one_of(st.none(), st.text(min_size=0, max_size=500)))
    thread = draw(st.one_of(st.none(), st.text(min_size=1, max_size=50)))
    ref = draw(st.one_of(st.none(), st.text(min_size=1, max_size=50)))
    extra = draw(st.one_of(
        st.just({}),
        st.dictionaries(
            st.text(min_size=1, max_size=10),
            st.text(max_size=50),
            max_size=5,
        ),
    ))
    return LXCFMessage(
        msg_type, nick,
        channel=channel, body=body,
        thread=thread, ref=ref, extra=extra,
    )


@st.composite
def lxcf_fields(draw) -> dict:
    """Generate a valid LXCF fields dict (output of to_fields)."""
    msg = draw(lxcf_message())
    return msg.to_fields()


@st.composite
def non_lxcf_fields(draw) -> dict:
    """Generate a fields dict that is NOT an LXCF payload."""
    return draw(st.one_of(
        st.just({}),
        st.just({FIELD_CUSTOM_TYPE: "NOT_LXCF", FIELD_CUSTOM_DATA: {}}),
        st.just({FIELD_CUSTOM_TYPE: None}),
        st.dictionaries(st.integers(), st.text(), max_size=5),
    ))


def identity_hash() -> st.SearchStrategy[bytes]:
    """Generate a 16-byte identity hash."""
    return st.binary(min_size=16, max_size=16)


def channel_hash() -> st.SearchStrategy[bytes]:
    """Generate a 16-byte Channel_Hash value."""
    return st.binary(min_size=16, max_size=16)


def subscriber_set() -> st.SearchStrategy[set[bytes]]:
    """Generate a set of 1-10 distinct 16-byte destination hashes."""
    return st.frozensets(
        st.binary(min_size=16, max_size=16),
        min_size=1,
        max_size=10,
    ).map(set)


@st.composite
def channel_envelope(draw) -> ChannelEnvelope:
    """Generate a valid ChannelEnvelope wrapping an arbitrary LXCFMessage."""
    msg = draw(lxcf_message())
    ch = draw(channel_hash())
    src = draw(identity_hash())
    fields = msg.to_fields()
    return ChannelEnvelope(
        channel_hash=ch,
        source_hash=src,
        custom_type=fields[FIELD_CUSTOM_TYPE],
        custom_data=fields[FIELD_CUSTOM_DATA],
    )


# ---------------------------------------------------------------------------
# Mock objects
# ---------------------------------------------------------------------------

class MockDestination:
    """Simulates an RNS.Destination for testing."""

    def __init__(self, hash_bytes: bytes = b"\x00" * 16):
        self.hash = hash_bytes


class MockPacket:
    """Simulates an RNS packet with destination_hash."""

    def __init__(self, destination_hash: bytes):
        self.destination_hash = destination_hash


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def local_client():
    """A Client in local mode (no RNS/LXMF)."""
    return Client(nick="testuser")


@pytest.fixture
def two_local_clients():
    """Two local clients for cross-dispatch testing."""
    return Client(nick="alice"), Client(nick="bob")


@pytest.fixture
def joined_client():
    """A local client already joined to #test."""
    c = Client(nick="testuser")
    ch = c.join("#test", announce=False)
    return c, ch


@pytest.fixture
def event_log():
    """Returns a list that captures events for assertion."""
    return []
