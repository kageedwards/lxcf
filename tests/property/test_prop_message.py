"""
Property tests for LXCFMessage serialization.

Properties validated:
  1. Round-Trip Preservation
  2. Stanza Key Validity
  3. Optional Field Omission
  4. is_lxcf Classification
  5. Invalid Type Rejection

Validates Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from lxcf.protocol import (
    FIELD_CUSTOM_TYPE,
    FIELD_CUSTOM_DATA,
    PROTOCOL_NAME,
    MessageType,
)
from lxcf.message import LXCFMessage

from tests.conftest import lxcf_message, lxcf_fields, non_lxcf_fields

REQUIRED_STANZA_KEYS = {"v", "t", "n"}
OPTIONAL_STANZA_KEYS = {"c", "b", "th", "r", "x"}
ALL_VALID_STANZA_KEYS = REQUIRED_STANZA_KEYS | OPTIONAL_STANZA_KEYS


# --- Property 1: Round-Trip Preservation ---

@given(msg=lxcf_message())
@settings(max_examples=500)
def test_message_round_trip(msg: LXCFMessage):
    """from_fields(msg.to_fields()) preserves all semantic fields."""
    fields = msg.to_fields()
    reconstructed = LXCFMessage.from_fields(fields)

    assert reconstructed.type == msg.type
    assert reconstructed.nick == msg.nick
    assert reconstructed.channel == msg.channel
    assert reconstructed.body == msg.body
    assert reconstructed.thread == msg.thread
    assert reconstructed.ref == msg.ref
    assert reconstructed.extra == msg.extra


# --- Property 2: Stanza Key Validity ---

@given(msg=lxcf_message())
@settings(max_examples=200)
def test_stanza_keys_are_valid(msg: LXCFMessage):
    """All stanza keys ⊆ {v, t, n, c, b, th, r, x} and ⊇ {v, t, n}."""
    fields = msg.to_fields()
    stanza = fields[FIELD_CUSTOM_DATA]

    assert set(stanza.keys()) <= ALL_VALID_STANZA_KEYS
    assert REQUIRED_STANZA_KEYS <= set(stanza.keys())


# --- Property 3: Optional Field Omission ---

@given(msg=lxcf_message())
@settings(max_examples=200)
def test_optional_fields_omitted_when_none(msg: LXCFMessage):
    """None-valued optional fields do not appear in the stanza."""
    fields = msg.to_fields()
    stanza = fields[FIELD_CUSTOM_DATA]

    if msg.channel is None:
        assert "c" not in stanza
    if msg.body is None:
        assert "b" not in stanza
    if msg.thread is None:
        assert "th" not in stanza
    if msg.ref is None:
        assert "r" not in stanza


@given(msg=lxcf_message())
@settings(max_examples=200)
def test_empty_extra_omits_x_key(msg: LXCFMessage):
    """Empty extra dict omits the 'x' key from the stanza."""
    fields = msg.to_fields()
    stanza = fields[FIELD_CUSTOM_DATA]

    if msg.extra == {}:
        assert "x" not in stanza


# --- Property 4: is_lxcf Classification ---

@given(fields=st.one_of(lxcf_fields(), non_lxcf_fields()))
@settings(max_examples=200)
def test_is_lxcf_classification(fields: dict):
    """is_lxcf returns True iff FIELD_CUSTOM_TYPE == 'LXCF'."""
    expected = fields.get(FIELD_CUSTOM_TYPE) == PROTOCOL_NAME
    assert LXCFMessage.is_lxcf(fields) == expected


# --- Property 5: Invalid Type Rejection ---

@given(bad_type=st.text(min_size=1, max_size=30).filter(lambda t: t not in MessageType.ALL))
@settings(max_examples=200)
def test_invalid_type_raises(bad_type: str):
    """ValueError raised for types not in MessageType.ALL."""
    try:
        LXCFMessage(bad_type, "testnick")
        assert False, f"Expected ValueError for type {bad_type!r}"
    except ValueError:
        pass
