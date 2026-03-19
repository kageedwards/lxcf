"""
LXCF Protocol Constants and Stanza Types

LXCF stanzas ride inside LXMF messages using the standard LXMF
FIELD_CUSTOM_TYPE / FIELD_CUSTOM_DATA mechanism, so they interoperate
cleanly with any LXMF client or propagation node.
"""

import hashlib

PROTOCOL_NAME    = "LXCF"
PROTOCOL_VERSION = 1

# We use the LXMF-native custom-data fields rather than inventing
# our own field ID.  These constants mirror LXMF.LXMF but are
# duplicated here so the library can be imported without LXMF
# installed (for offline tests / message construction).
FIELD_CUSTOM_TYPE = 0xFB
FIELD_CUSTOM_DATA = 0xFC

# Channel Envelope routing fields (LXMF user-defined field range)
FIELD_CHANNEL_HASH = 0xFD
FIELD_SOURCE_HASH  = 0xFE


class MessageType:
    """IRC-inspired stanza types for mesh messaging."""
    MESSAGE   = "message"    # Channel / group message
    PRIVMSG   = "privmsg"    # Direct (1:1) message
    JOIN      = "join"       # Join a channel
    LEAVE     = "leave"      # Leave a channel
    NICK      = "nick"       # Nickname announcement / change
    TOPIC     = "topic"      # Set channel topic
    EMOTE     = "emote"      # /me style action
    ANNOUNCE  = "announce"   # Presence broadcast
    # REACTION  = "reaction"   # Emoji / response to a message
    # QUERY     = "query"      # IQ-style request
    # REPLY     = "reply"      # IQ-style response
    # NAMES     = "names"      # Request / response for channel member list
    # PING      = "ping"       # Keepalive / latency probe
    # PONG      = "pong"       # Keepalive response

    ALL = {
        MESSAGE, PRIVMSG, JOIN, LEAVE, NICK, TOPIC,
        EMOTE, ANNOUNCE,
    }


def derive_channel_hash(name: str, key: bytes | None = None) -> bytes:
    """
    Derive a 16-byte Channel_Hash from a channel name and optional key.

    SHA-256(name.encode("utf-8") + (key or b"")) truncated to 16 bytes.
    """
    return hashlib.sha256(name.encode("utf-8") + (key or b"")).digest()[:16]
