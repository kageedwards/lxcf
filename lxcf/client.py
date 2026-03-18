"""
LXCF Client — IRC-style messaging as a pure layer over LXMF.

The client never imports RNS at module level.  All network interaction
goes through LXMF's LXMRouter and LXMessage APIs.  RNS is only
imported inside methods that need to create Destination objects, and
only when a live router is present.

Usage — connected (caller owns the LXMF router)::

    import RNS, LXMF, lxcf

    reticulum = RNS.Reticulum()
    identity  = RNS.Identity()
    router    = LXMF.LXMRouter(identity=identity, storagepath="./store")
    dest      = router.register_delivery_identity(identity, display_name="kage")

    client = lxcf.Client(router=router, destination=dest, nick="kage")
    ch = client.join("#mesh")
    ch.send("Hello mesh")

Usage — local-only (no LXMF, for tests)::

    client = lxcf.Client(nick="alice")
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Callable

from lxcf.channel import Channel
from lxcf.envelope import ChannelEnvelope, encrypt_custom_data, decrypt_custom_data
from lxcf.events import EventBus
from lxcf.message import LXCFMessage
from lxcf.protocol import FIELD_CUSTOM_DATA, FIELD_CUSTOM_TYPE, PROTOCOL_NAME, MessageType, derive_channel_hash

log = logging.getLogger("lxcf")

# LXMF app name / aspect used for group destinations.
# This keeps LXCF group traffic in its own namespace.
LXCF_APP_NAME = "lxcf"
LXCF_GROUP_ASPECT = "channel"


def channel_id(name: str, key: bytes | None = None) -> str:
    """
    Return a unique internal identifier for a channel.

    Open channels use the bare name (e.g. ``"#mesh"``).
    Keyed channels include a short hash of the key so that
    ``#mesh`` on different subnets get separate entries.
    """
    if key is None:
        return name
    return f"{name}@{hashlib.sha256(key).hexdigest()[:8]}"


class Client:
    """
    LXCF client.

    Parameters
    ----------
    router : LXMF.LXMRouter | None
        An already-initialised LXMRouter.  If *None* the client
        runs in local-only mode.
    destination : RNS.Destination | None
        The SINGLE delivery destination returned by
        ``router.register_delivery_identity()``.
    nick : str
        Display name.
    """

    def __init__(
        self,
        router=None,
        destination=None,
        nick: str = "anon",
    ):
        self._router = router
        self._destination = destination
        self.nick = nick
        self.events = EventBus()

        self.channels: dict[str, Channel] = {}  # channel_id -> Channel
        self._dest_to_cid: dict[bytes, str] = {}  # destination hash -> channel_id
        self._channel_hash_to_cid: dict[bytes, str] = {}  # Channel_Hash -> channel_id
        self.trusted: set[bytes] = set()
        self.blocked: set[bytes] = set()

        if self._router is not None:
            self._router.register_delivery_callback(self._on_lxmf_delivery)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def address(self) -> str | None:
        """Hex destination hash, or None in local mode."""
        if self._destination is not None:
            try:
                return self._destination.hash.hex()
            except Exception:
                pass
        return None

    @property
    def connected(self) -> bool:
        return self._router is not None

    # ------------------------------------------------------------------
    # Group destination factory
    # ------------------------------------------------------------------

    def _make_group_destination(self, channel_name: str, key: bytes | None = None):
        """
        Create an RNS GROUP destination for a channel.

        Both the group identity and the symmetric encryption key are
        derived deterministically from the channel name (for open
        channels) so that any client joining the same name arrives
        at the same destination hash and can decrypt traffic.

        For private channels, pass *key* (32 bytes) to use as the
        symmetric key instead of deriving one from the name.

        Returns an RNS.Destination or None if not connected.
        """
        if self._router is None:
            return None

        import RNS

        # Derive 64 bytes of key material from the channel name.
        # RNS.Identity.from_bytes needs 64 bytes: 32 for X25519 + 32 for Ed25519.
        if key:
            seed = f"lxcf:channel:{channel_name}:{key.hex()}".encode("utf-8")
        else:
            seed = f"lxcf:channel:{channel_name}".encode("utf-8")

        identity_key_material = hashlib.sha512(seed).digest()  # 64 bytes

        group_identity = RNS.Identity.from_bytes(identity_key_material)
        if group_identity is None:
            raise RuntimeError(f"Failed to create group identity for {channel_name}")

        dest = RNS.Destination(
            group_identity,
            RNS.Destination.IN,
            RNS.Destination.GROUP,
            LXCF_APP_NAME,
            LXCF_GROUP_ASPECT,
        )

        # Derive or load the symmetric key for GROUP encryption.
        if key is not None:
            dest.load_private_key(key)
        else:
            # Deterministic symmetric key from channel name,
            # using a different derivation than the identity.
            sym_key = hashlib.sha256(
                f"lxcf:channel_key:{channel_name}".encode("utf-8")
            ).digest()
            dest.load_private_key(sym_key)

        dest.set_packet_callback(self._on_group_packet)

        return dest

    # ------------------------------------------------------------------
    # Channel management
    # ------------------------------------------------------------------

    def join(self, channel_name: str, key: bytes | None = None, announce: bool = True, hub: bytes | None = None) -> Channel:
        """
        Join (or create) a channel.

        Parameters
        ----------
        channel_name : str
            Channel to join.
        key : bytes | None
            32-byte symmetric key for private channels.
        announce : bool
            If True (default), broadcast a JOIN stanza so other
            members know you're here.  Set to False to join silently.
        hub : bytes | None
            16-byte destination hash of the hub to route through.
            When set, channel messages are sent as Channel Envelopes
            to the hub via LXMF SINGLE delivery.  When None, the
            channel uses the existing GROUP destination behavior.
        """
        cid = channel_id(channel_name, key)
        if cid in self.channels:
            return self.channels[cid]

        group_dest = self._make_group_destination(channel_name, key=key)
        ch = Channel(channel_name, self, group_destination=group_dest)
        ch._cid = cid
        ch.channel_hash = derive_channel_hash(channel_name, key)
        ch.hub_hash = hub
        ch.key = key
        self.channels[cid] = ch
        self._channel_hash_to_cid[ch.channel_hash] = cid

        if group_dest is not None:
            self._dest_to_cid[group_dest.hash] = cid

        ch._member_join(self.nick, source_hash=getattr(self._destination, "hash", None))

        if announce:
            join_msg = LXCFMessage.join(self.nick, channel_name)
            if hub is not None:
                self._send_to_hub(ch, join_msg)
            else:
                self._send_to_channel(ch, join_msg)
        self.events.emit("join", ch, self.nick)
        log.info("Joined %s%s", channel_name, "" if announce else " (silent)")
        return ch

    def leave(self, channel_id_or_name: str, announce: bool = True):
        """Leave a channel, optionally broadcasting a LEAVE stanza."""
        ch = self.channels.pop(channel_id_or_name, None)
        if ch is None:
            return
        if announce:
            leave_msg = LXCFMessage.leave(self.nick, ch.name)
            if ch.hub_hash is not None:
                self._send_to_hub(ch, leave_msg)
            else:
                self._send_to_channel(ch, leave_msg)

        # Deregister the GROUP destination so it can be re-created on rejoin.
        if ch.destination is not None:
            self._dest_to_cid.pop(ch.destination.hash, None)
            try:
                import RNS
                RNS.Transport.deregister_destination(ch.destination)
            except Exception:
                pass

        self._channel_hash_to_cid.pop(ch.channel_hash, None)

        self.events.emit("leave", ch, self.nick)
        log.info("Left %s%s", ch.name, "" if announce else " (silent)")

    def change_nick(self, new_nick: str, announce: bool = True):
        """
        Change nickname, update member lists, and optionally
        broadcast a NICK stanza to all joined channels.
        """
        old_nick = self.nick
        self.nick = new_nick

        for ch in self.channels.values():
            ts = ch.members.pop(old_nick, None)
            ch.members[new_nick] = ts or time.time()
            h = ch.member_hashes.pop(old_nick, None)
            if h:
                ch.member_hashes[new_nick] = h

            if announce:
                nick_msg = LXCFMessage(
                    MessageType.NICK, new_nick,
                    channel=ch.name,
                )
                self._send_to_channel(ch, nick_msg)

        self.events.emit("nick", old_nick, new_nick)
        log.info("Nick changed: %s -> %s%s", old_nick, new_nick,
                 "" if announce else " (silent)")


    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send(self, channel_id_or_name: str, body: str, **kw) -> LXCFMessage:
        """Send a message to a joined channel by channel_id or name."""
        ch = self.channels.get(channel_id_or_name)
        if ch is None:
            # Fall back to name match (for open channels where cid == name).
            for c in self.channels.values():
                if c.name == channel_id_or_name:
                    ch = c
                    break
        if ch is None:
            raise ValueError(f"Not in channel {channel_id_or_name}")
        return ch.send(body, **kw)

    def privmsg(self, destination_hash: bytes | str, body: str, **kw) -> LXCFMessage:
        """Send a direct message to a destination hash."""
        if isinstance(destination_hash, str):
            destination_hash = bytes.fromhex(destination_hash)
        msg = LXCFMessage.privmsg(self.nick, body, **kw)
        self._send_direct(destination_hash, msg)
        return msg

    def announce_presence(self, channels: list[str] | None = None):
        """Broadcast a presence announce to all joined channels."""
        ch_names = channels or list(self.channels.keys())
        msg = LXCFMessage.announce(self.nick, channels=ch_names)
        self._broadcast(msg)
        self.events.emit("announce", self.nick, ch_names)

    # ------------------------------------------------------------------
    # Trust management
    # ------------------------------------------------------------------

    def trust(self, identity_hash: bytes):
        self.trusted.add(identity_hash)
        self.blocked.discard(identity_hash)

    def block(self, identity_hash: bytes):
        self.blocked.add(identity_hash)
        self.trusted.discard(identity_hash)

    def is_blocked(self, identity_hash: bytes) -> bool:
        return identity_hash in self.blocked

    # ------------------------------------------------------------------
    # Decorator shortcuts
    # ------------------------------------------------------------------

    def on_message(self, fn: Callable):
        self.events.on("message", fn)
        return fn

    def on_privmsg(self, fn: Callable):
        self.events.on("privmsg", fn)
        return fn

    def on_join(self, fn: Callable):
        self.events.on("join", fn)
        return fn

    def on_leave(self, fn: Callable):
        self.events.on("leave", fn)
        return fn

    def on_announce(self, fn: Callable):
        self.events.on("announce", fn)
        return fn

    # ------------------------------------------------------------------
    # Internal transport
    # ------------------------------------------------------------------

    def _send_to_channel(self, channel: Channel, msg: LXCFMessage):
        """Dispatch a message to a channel."""
        channel._record(msg)

        # Only emit chat-style events here. Join/leave/announce are
        # emitted explicitly by join()/leave()/announce_presence() with
        # their own signatures, so we skip them to avoid double-firing.
        if msg.type not in (MessageType.JOIN, MessageType.LEAVE, MessageType.ANNOUNCE):
            self.events.emit(msg.type, channel, msg)

        if self._router is not None:
            if channel.hub_hash is not None:
                self._send_to_hub(channel, msg)
            elif channel.destination is not None:
                self._lxmf_send_group(channel, msg)

    def _send_to_hub(self, channel: Channel, msg: LXCFMessage):
        """Wrap an LXCFMessage in a Channel Envelope and send to the hub."""
        if self._router is None or channel.hub_hash is None:
            return

        msg_fields = msg.to_fields()
        custom_data = msg_fields[FIELD_CUSTOM_DATA]

        # Encrypt the inner stanza for private channels
        if channel.key is not None:
            custom_data = encrypt_custom_data(custom_data, channel.key)

        envelope = ChannelEnvelope(
            channel_hash=channel.channel_hash,
            source_hash=self._destination.hash,
            custom_type=msg_fields[FIELD_CUSTOM_TYPE],
            custom_data=custom_data,
        )

        try:
            import RNS
            import LXMF

            fields = envelope.to_fields()
            dest_hash = channel.hub_hash

            recipient_identity = RNS.Identity.recall(dest_hash)
            if recipient_identity is None:
                RNS.Transport.request_path(dest_hash)
                log.info("Path to hub %s unknown, requested — message queued", dest_hash.hex())

                lxm = LXMF.LXMessage(
                    None,
                    self._destination,
                    "",
                    fields=fields,
                    destination_hash=dest_hash,
                    desired_method=LXMF.LXMessage.DIRECT,
                )
            else:
                dest = RNS.Destination(
                    recipient_identity,
                    RNS.Destination.OUT,
                    RNS.Destination.SINGLE,
                    "lxmf", "delivery",
                )
                lxm = LXMF.LXMessage(
                    dest,
                    self._destination,
                    "",
                    fields=fields,
                    desired_method=LXMF.LXMessage.DIRECT,
                )

            self._router.handle_outbound(lxm)
        except Exception as exc:
            log.error("LXMF send to hub failed: %s", exc)

    def _send_direct(self, destination_hash: bytes, msg: LXCFMessage):
        """Send a direct LXMF message to a single destination."""
        self.events.emit("privmsg", destination_hash, msg)

        if self._router is not None:
            self._lxmf_send_direct(destination_hash, msg)

    def _broadcast(self, msg: LXCFMessage):
        """Send a message to all joined channels."""
        for ch in self.channels.values():
            self._send_to_channel(ch, msg)

    # ------------------------------------------------------------------
    # LXMF integration
    # ------------------------------------------------------------------

    def _lxmf_send_group(self, channel: Channel, msg: LXCFMessage):
        """
        Send an LXMF message to a channel's GROUP destination.

        Uses OPPORTUNISTIC delivery — the message is packed into a
        single Reticulum packet addressed to the group, which any
        node listening on that group destination will receive.
        """
        try:
            import LXMF

            lxm = LXMF.LXMessage(
                channel.destination,     # GROUP destination
                self._destination,       # our SINGLE source
                msg.body or "",          # content (fallback for non-LXCF clients)
                fields=msg.to_fields(),
                desired_method=LXMF.LXMessage.OPPORTUNISTIC,
            )
            self._router.handle_outbound(lxm)
        except Exception as exc:
            log.error("LXMF group send to %s failed: %s", channel.name, exc)

    def _lxmf_send_direct(self, destination_hash: bytes, msg: LXCFMessage):
        """
        Send an LXMF message to a single destination by hash.

        Attempts DIRECT delivery (over a Reticulum link).  If the
        path is unknown LXMF will request it and retry automatically.
        """
        try:
            import RNS
            import LXMF

            # Recall the identity for this destination hash.
            # If unknown, LXMF will queue the message and request
            # the path automatically.
            recipient_identity = RNS.Identity.recall(destination_hash)
            if recipient_identity is None:
                RNS.Transport.request_path(destination_hash)
                log.info("Path to %s unknown, requested — message queued", destination_hash.hex())

                # Queue with destination_hash so LXMF can resolve later
                lxm = LXMF.LXMessage(
                    None,
                    self._destination,
                    msg.body or "",
                    fields=msg.to_fields(),
                    destination_hash=destination_hash,
                    desired_method=LXMF.LXMessage.DIRECT,
                )
            else:
                dest = RNS.Destination(
                    recipient_identity,
                    RNS.Destination.OUT,
                    RNS.Destination.SINGLE,
                    "lxmf", "delivery",
                )
                lxm = LXMF.LXMessage(
                    dest,
                    self._destination,
                    msg.body or "",
                    fields=msg.to_fields(),
                    desired_method=LXMF.LXMessage.DIRECT,
                )

            self._router.handle_outbound(lxm)
        except Exception as exc:
            log.error("LXMF direct send failed: %s", exc)

    # ------------------------------------------------------------------
    # Inbound handlers
    # ------------------------------------------------------------------

    def _on_lxmf_delivery(self, message):
        """
        Callback from LXMRouter for SINGLE destination deliveries
        (direct messages and hub-relayed Channel Envelopes).
        """
        try:
            fields = message.fields if hasattr(message, "fields") else {}

            # --- Channel Envelope path (hub-relayed messages) ---
            if ChannelEnvelope.is_envelope(fields):
                envelope = ChannelEnvelope.from_fields(fields)

                # Discard if no matching local channel
                cid = self._channel_hash_to_cid.get(envelope.channel_hash)
                if cid is None:
                    return

                # Suppress self-echo
                if self._destination and envelope.source_hash == self._destination.hash:
                    return

                # Blocked sender check
                if envelope.source_hash and self.is_blocked(envelope.source_hash):
                    log.debug("Blocked envelope from %s", envelope.source_hash.hex())
                    return

                # Decrypt encrypted envelopes (private channels)
                channel = self.channels.get(cid)
                if channel is None:
                    return

                if isinstance(envelope.custom_data, bytes):
                    if channel.key is None:
                        log.warning("Received encrypted envelope but no key for channel %s", cid)
                        return
                    try:
                        envelope.custom_data = decrypt_custom_data(envelope.custom_data, channel.key)
                    except Exception:
                        log.warning("Decryption failed for channel %s — wrong key?", cid)
                        return

                msg = envelope.unwrap()

                self._dispatch_inbound(msg, source_hash=envelope.source_hash, target_channel=channel)
                return

            # --- Regular direct LXCF message path ---
            if not LXCFMessage.is_lxcf(fields):
                return

            msg = LXCFMessage.from_fields(fields)
            source_hash = getattr(message, "source_hash", None)

            if source_hash and self.is_blocked(source_hash):
                log.debug("Blocked message from %s", source_hash.hex())
                return

            self._dispatch_inbound(msg, source_hash=source_hash)
        except Exception as exc:
            log.warning("Failed to process inbound LXCF message: %s", exc)

    def _on_group_packet(self, data, packet):
        """
        Callback from an RNS GROUP destination when a packet arrives.

        Opportunistic LXMF packets omit the destination hash prefix,
        so the decrypted data layout is:
            source_hash(16) + signature(64) + msgpack_payload

        We prepend the destination hash (from the packet) so that
        LXMF.LXMessage.unpack_from_bytes receives the full format it
        expects:
            dest_hash(16) + source_hash(16) + signature(64) + payload
        """
        try:
            import LXMF

            # Reconstruct the full LXMF frame expected by unpack_from_bytes
            dest_hash = getattr(packet, "destination_hash", None)
            if dest_hash is None:
                return
            full_data = bytes(dest_hash) + bytes(data)

            lxm = LXMF.LXMessage.unpack_from_bytes(full_data)
            if lxm is None:
                return

            fields = lxm.fields if hasattr(lxm, "fields") else {}
            if not LXCFMessage.is_lxcf(fields):
                return

            msg = LXCFMessage.from_fields(fields)
            source_hash = getattr(lxm, "source_hash", None)

            # Don't echo our own messages back
            if source_hash and self._destination:
                if source_hash == self._destination.hash:
                    return

            if source_hash and self.is_blocked(source_hash):
                log.debug("Blocked group message from %s", source_hash.hex())
                return

            # Resolve the target channel from the destination that
            # received this packet, not from the channel name in the
            # stanza (which may be ambiguous across subnets).
            dest_hash = getattr(packet, "destination_hash", None)
            target_ch = None
            if dest_hash:
                cid = self._dest_to_cid.get(dest_hash)
                if cid:
                    target_ch = self.channels.get(cid)

            self._dispatch_inbound(msg, source_hash=source_hash, target_channel=target_ch)
        except Exception as exc:
            log.warning("Failed to process inbound group packet: %s", exc)

    def _dispatch_inbound(self, msg: LXCFMessage, source_hash: bytes | None = None, target_channel: "Channel | None" = None):
        """Route an inbound LXCFMessage to the right event handlers."""

        def _resolve(name: str | None) -> "Channel | None":
            """Return target_channel if set, else first channel matching name."""
            if target_channel is not None:
                return target_channel
            if name is None:
                return None
            # Fall back to name scan (for local mode / direct deliveries).
            for ch in self.channels.values():
                if ch.name == name:
                    return ch
            return None

        if msg.type == MessageType.MESSAGE:
            ch = _resolve(msg.channel)
            if ch:
                ch._record(msg, source_hash=source_hash)
            self.events.emit("message", ch, msg)

        elif msg.type == MessageType.PRIVMSG:
            self.events.emit("privmsg", source_hash, msg)

        elif msg.type == MessageType.JOIN:
            ch = _resolve(msg.channel)
            if ch:
                ch._member_join(msg.nick, msg.timestamp, source_hash=source_hash)
            self.events.emit("join", ch, msg.nick)

        elif msg.type == MessageType.LEAVE:
            ch = _resolve(msg.channel)
            if ch:
                ch._member_leave(msg.nick)
            self.events.emit("leave", ch, msg.nick)

        elif msg.type == MessageType.NICK:
            ch = _resolve(msg.channel)
            if ch and source_hash:
                old_nick = None
                for nick, h in ch.member_hashes.items():
                    if h == source_hash:
                        old_nick = nick
                        break
                if old_nick:
                    ts = ch.members.pop(old_nick, None)
                    ch.members[msg.nick] = ts or msg.timestamp
                    ch.member_hashes.pop(old_nick, None)
                else:
                    ch.members[msg.nick] = msg.timestamp
                ch.member_hashes[msg.nick] = source_hash
                self.events.emit("nick", old_nick or msg.nick, msg.nick)
            else:
                self.events.emit("nick", msg.nick, msg.nick)

        elif msg.type == MessageType.TOPIC:
            ch = _resolve(msg.channel)
            if ch:
                ch.topic = msg.body
            self.events.emit("topic", ch, msg)

        elif msg.type == MessageType.EMOTE:
            ch = _resolve(msg.channel)
            if ch:
                ch._record(msg, source_hash=source_hash)
            self.events.emit("emote", ch, msg)

        elif msg.type == MessageType.ANNOUNCE:
            self.events.emit("announce", msg.nick, msg.extra.get("channels", []))

        else:
            ch = _resolve(msg.channel) if msg.channel else None
            self.events.emit(msg.type, ch, msg)

    def __repr__(self):
        mode = "connected" if self.connected else "local"
        return f"<LXCF.Client nick={self.nick!r} mode={mode} channels={list(self.channels.keys())}>"
