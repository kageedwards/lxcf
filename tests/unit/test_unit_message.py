"""Unit tests for lxcf.message.LXCFMessage."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lxcf.protocol import (
    FIELD_CUSTOM_TYPE,
    FIELD_CUSTOM_DATA,
    PROTOCOL_NAME,
    MessageType,
)
from lxcf.message import LXCFMessage


# ------------------------------------------------------------------
# Convenience constructor correctness (Property 14)
# ------------------------------------------------------------------

CONSTRUCTOR_MAP = {
    "chat":     (LXCFMessage.chat,     MessageType.MESSAGE,  {"nick": "a", "channel": "#c", "body": "hi"}),
    "privmsg":  (LXCFMessage.privmsg,  MessageType.PRIVMSG,  {"nick": "a", "body": "hi"}),
    "join":     (LXCFMessage.join,     MessageType.JOIN,     {"nick": "a", "channel": "#c"}),
    "leave":    (LXCFMessage.leave,    MessageType.LEAVE,    {"nick": "a", "channel": "#c"}),
    "emote":    (LXCFMessage.emote,    MessageType.EMOTE,    {"nick": "a", "channel": "#c", "body": "waves"}),
    "announce": (LXCFMessage.announce, MessageType.ANNOUNCE, {"nick": "a"}),
    "topic":    (LXCFMessage.topic,    MessageType.TOPIC,    {"nick": "a", "channel": "#c", "body": "new topic"}),
}


@pytest.mark.parametrize("name,spec", list(CONSTRUCTOR_MAP.items()), ids=list(CONSTRUCTOR_MAP.keys()))
def test_convenience_constructor_sets_correct_type(name, spec):
    ctor, expected_type, kwargs = spec
    msg = ctor(**kwargs)
    assert msg.type == expected_type


@pytest.mark.parametrize("name,spec", list(CONSTRUCTOR_MAP.items()), ids=list(CONSTRUCTOR_MAP.keys()))
def test_convenience_constructor_assigns_fields(name, spec):
    ctor, _, kwargs = spec
    msg = ctor(**kwargs)
    assert msg.nick == kwargs["nick"]
    if "channel" in kwargs:
        assert msg.channel == kwargs["channel"]
    if "body" in kwargs:
        assert msg.body == kwargs["body"]


# ------------------------------------------------------------------
# from_fields error cases
# ------------------------------------------------------------------

def test_from_fields_raises_for_non_lxcf_custom_type():
    fields = {FIELD_CUSTOM_TYPE: "NOT_LXCF", FIELD_CUSTOM_DATA: {"v": 1, "t": "message", "n": "x"}}
    with pytest.raises(ValueError, match="Not an LXCF payload"):
        LXCFMessage.from_fields(fields)


def test_from_fields_raises_when_custom_data_missing():
    fields = {FIELD_CUSTOM_TYPE: PROTOCOL_NAME}
    with pytest.raises(ValueError, match="custom_data"):
        LXCFMessage.from_fields(fields)


# ------------------------------------------------------------------
# __repr__
# ------------------------------------------------------------------

def test_repr_contains_type_and_nick():
    msg = LXCFMessage.chat("alice", "#general", "hello world")
    r = repr(msg)
    assert "LXCFMessage" in r
    assert "message" in r
    assert "alice" in r


def test_repr_truncates_long_body():
    msg = LXCFMessage.chat("alice", "#general", "x" * 100)
    r = repr(msg)
    # Body preview should be truncated at 40 chars + ellipsis
    assert "\u2026" in r


# ------------------------------------------------------------------
# Property 14: Convenience Constructor Correctness (Hypothesis)
# ------------------------------------------------------------------

@given(
    nick=st.text(min_size=1, max_size=20),
    channel=st.text(min_size=1, max_size=20).map(lambda s: f"#{s}"),
    body=st.text(min_size=1, max_size=100),
)
@settings(max_examples=200)
def test_chat_constructor_property(nick, channel, body):
    msg = LXCFMessage.chat(nick, channel, body)
    assert msg.type == MessageType.MESSAGE
    assert msg.nick == nick
    assert msg.channel == channel
    assert msg.body == body
