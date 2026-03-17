"""Unit tests for lxcf.protocol constants and MessageType."""

from lxcf.protocol import (
    FIELD_CUSTOM_TYPE,
    FIELD_CUSTOM_DATA,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    MessageType,
)


def test_message_type_all_contains_exactly_8_types():
    assert len(MessageType.ALL) == 8
    expected = {
        MessageType.MESSAGE,
        MessageType.PRIVMSG,
        MessageType.JOIN,
        MessageType.LEAVE,
        MessageType.NICK,
        MessageType.TOPIC,
        MessageType.EMOTE,
        MessageType.ANNOUNCE,
    }
    assert MessageType.ALL == expected


def test_field_custom_type_value():
    assert FIELD_CUSTOM_TYPE == 0xFB


def test_field_custom_data_value():
    assert FIELD_CUSTOM_DATA == 0xFC


def test_protocol_name():
    assert PROTOCOL_NAME == "LXCF"


def test_protocol_version():
    assert PROTOCOL_VERSION == 1
