"""
LXCF Channel Envelope — wraps an LXCF stanza with channel routing metadata
for hub-relayed delivery.

A Channel Envelope adds a Channel_Hash (identifying the target channel) and
a source_hash (the original sender) alongside the standard FIELD_CUSTOM_TYPE /
FIELD_CUSTOM_DATA fields.  The Hub routes solely on the cleartext Channel_Hash
without inspecting the inner stanza.
"""

from __future__ import annotations

import base64

import msgpack
from cryptography.fernet import Fernet, InvalidToken

from lxcf.protocol import (
    FIELD_CUSTOM_TYPE,
    FIELD_CUSTOM_DATA,
    FIELD_CHANNEL_HASH,
    FIELD_SOURCE_HASH,
    PROTOCOL_NAME,
)
from lxcf.message import LXCFMessage


class ChannelEnvelope:
    """
    Wraps an LXCF stanza with channel routing metadata.

    Attributes
    ----------
    channel_hash : bytes
        16-byte Channel_Hash identifying the target channel.
    source_hash : bytes
        16-byte destination hash of the original sender.
    custom_type : str
        The FIELD_CUSTOM_TYPE value (always "LXCF").
    custom_data : dict | bytes
        The FIELD_CUSTOM_DATA value — a stanza dict for open channels,
        or encrypted bytes for private channels.
    """

    __slots__ = ("channel_hash", "source_hash", "custom_type", "custom_data")

    def __init__(
        self,
        channel_hash: bytes,
        source_hash: bytes,
        custom_type: str,
        custom_data: dict | bytes,
    ):
        self.channel_hash = channel_hash
        self.source_hash = source_hash
        self.custom_type = custom_type
        self.custom_data = custom_data

    def to_fields(self) -> dict:
        """Serialize to an LXMF fields dict for transmission."""
        return {
            FIELD_CHANNEL_HASH: self.channel_hash,
            FIELD_SOURCE_HASH: self.source_hash,
            FIELD_CUSTOM_TYPE: self.custom_type,
            FIELD_CUSTOM_DATA: self.custom_data,
        }

    @classmethod
    def from_fields(cls, fields: dict) -> "ChannelEnvelope":
        """Reconstruct from an LXMF fields dict."""
        ch = fields.get(FIELD_CHANNEL_HASH)
        if not isinstance(ch, bytes) or len(ch) != 16:
            raise ValueError(
                f"FIELD_CHANNEL_HASH must be 16 bytes, got {type(ch).__name__}"
            )
        src = fields.get(FIELD_SOURCE_HASH)
        if not isinstance(src, bytes) or len(src) != 16:
            raise ValueError(
                f"FIELD_SOURCE_HASH must be 16 bytes, got {type(src).__name__}"
            )
        ctype = fields.get(FIELD_CUSTOM_TYPE)
        if ctype is None:
            raise ValueError("Missing FIELD_CUSTOM_TYPE")
        cdata = fields.get(FIELD_CUSTOM_DATA)
        if cdata is None:
            raise ValueError("Missing FIELD_CUSTOM_DATA")
        return cls(
            channel_hash=ch,
            source_hash=src,
            custom_type=ctype,
            custom_data=cdata,
        )

    def unwrap(self) -> LXCFMessage:
        """
        Extract the inner LXCFMessage.

        Raises ValueError if custom_data is encrypted bytes rather than
        a stanza dict.
        """
        if isinstance(self.custom_data, bytes):
            raise ValueError("Cannot unwrap encrypted envelope; decrypt first")
        return LXCFMessage.from_fields({
            FIELD_CUSTOM_TYPE: self.custom_type,
            FIELD_CUSTOM_DATA: self.custom_data,
        })

    @staticmethod
    def is_envelope(fields: dict) -> bool:
        """Return True if the fields dict contains a Channel Envelope."""
        return FIELD_CHANNEL_HASH in fields

    def __repr__(self) -> str:
        return (
            f"<ChannelEnvelope ch={self.channel_hash.hex()[:8]}… "
            f"src={self.source_hash.hex()[:8]}… "
            f"type={self.custom_type!r}>"
        )


# ---------------------------------------------------------------------------
# Private-channel encryption helpers
# ---------------------------------------------------------------------------

def encrypt_custom_data(custom_data: dict, key: bytes) -> bytes:
    """
    Encrypt FIELD_CUSTOM_DATA with the channel's symmetric key.

    Serialises *custom_data* with msgpack, then encrypts the result
    using Fernet (AES-128-CBC + HMAC-SHA256).  The *key* must be
    exactly 32 bytes; it is base64-encoded to produce the 44-char
    Fernet key that the library expects.
    """
    plaintext = msgpack.packb(custom_data, use_bin_type=True)
    fernet_key = base64.urlsafe_b64encode(key)
    return Fernet(fernet_key).encrypt(plaintext)


def decrypt_custom_data(ciphertext: bytes, key: bytes) -> dict:
    """
    Decrypt FIELD_CUSTOM_DATA with the channel's symmetric key.

    Reverses :func:`encrypt_custom_data`: Fernet-decrypts *ciphertext*
    and unpacks the msgpack payload back into a dict.

    Raises ``cryptography.fernet.InvalidToken`` if the key is wrong
    or the ciphertext is corrupted.
    """
    fernet_key = base64.urlsafe_b64encode(key)
    plaintext = Fernet(fernet_key).decrypt(ciphertext)
    return msgpack.unpackb(plaintext, raw=False)
