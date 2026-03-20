"""
LXCF Client — IRC-style messaging as a pure layer over LXMF.

The client never imports RNS at module level.  All network interaction
goes through LXMF's LXMRouter and LXMessage APIs.  RNS is only
imported inside methods that need to create Destination objects, and
only when a live router is present.

All channel traffic is routed through hubs via LXMF SINGLE
destinations.  Each hub is a daemon that receives Channel Envelopes
and relays them to subscribers.

Usage — connected (caller owns the LXMF router)::

    import RNS, LXMF, lxcf

    reticulum = RNS.Reticulum()
    identity  = RNS.Identity()
    router    = LXMF.LXMRouter(identity=identity, storagepath="./store")
    dest      = router.register_delivery_identity(identity, display_name="kage")

    client = lxcf.Client(router=router, destination=dest, nick="kage")
    ch = client.join("#mesh", hub=hub_hash)
    ch.send("Hello mesh")

Usage — local-only (no LXMF, for tests)::

    client = lxcf.Client(nick="alice")
"""

from __future__ import annotations

import hashlib
import logging
import sys
import time
from typing import Callable

from lxcf.channel import Channel
from lxcf.envelope import ChannelEnvelope, encrypt_custom_data, decrypt_custom_data
from lxcf.events import EventBus
from lxcf.message import LXCFMessage
from lxcf.protocol import FIELD_CUSTOM_DATA, FIELD_CUSTOM_TYPE, PROTOCOL_NAME, MessageType, derive_channel_hash

log = logging.getLogger("lxcf")


def _dbg(msg: str) -> None:
    """Debug print to stderr (safe for bridge stdout protocol). Remove for production."""
    print(msg, file=sys.stderr, flush=True)


def channel_id(name: str, hub: bytes | None = None) -> str:
    """
    Return a unique internal identifier for a channel.

    The CID is ``#name@XXXXXXXX`` where ``XXXXXXXX`` is the first 8 hex
    chars of the hub destination hash.  When no hub is provided (local
    mode), the bare channel name is returned.
    """
    if hub is None:
        return name
    return f"{name}@{hub.hex()[:8]}"


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
            32-byte symmetric passphrase key for private channels.
        announce : bool
            If True (default), broadcast a JOIN stanza so other
            members know you're here.  Set to False to join silently.
        hub : bytes | None
            16-byte destination hash of the hub to route through.
            All channel traffic is sent as Channel Envelopes to the
            hub via LXMF SINGLE delivery.
        """
        cid = channel_id(channel_name, hub)
        if cid in self.channels:
            return self.channels[cid]

        ch = Channel(channel_name, self)
        ch._cid = cid
        ch.channel_hash = derive_channel_hash(channel_name, key)
        ch.hub_hash = hub
        ch.key = key
        self.channels[cid] = ch
        self._channel_hash_to_cid[ch.channel_hash] = cid

        ch._member_join(self.nick, source_hash=getattr(self._destination, "hash", None))

        # DEBUG: remove for production
        dest_hex = self._destination.hash.hex() if self._destination else "none"
        _dbg(f"[client-debug] JOIN: cid={cid} ch_hash={ch.channel_hash.hex()[:8]} our_dest={dest_hex} hub={hub.hex() if hub else 'none'}")

        if announce:
            join_msg = LXCFMessage.join(self.nick, channel_name)
            if hub is not None:
                self._send_to_hub(ch, join_msg)
        self.events.emit("join", ch, self.nick)
        log.info("Joined %s%s", channel_name, "" if announce else " (silent)")
        return ch

    def leave(self, channel_id_or_name: str, announce: bool = True):
        """Leave a channel, optionally broadcasting a LEAVE stanza."""
        ch = self.channels.pop(channel_id_or_name, None)
        if ch is None:
            return

        # DEBUG: remove for production
        _dbg(f"[client-debug] LEAVE: cid={channel_id_or_name} ch_hash={ch.channel_hash.hex()[:8]}")

        if announce:
            leave_msg = LXCFMessage.leave(self.nick, ch.name)
            if ch.hub_hash is not None:
                self._send_to_hub(ch, leave_msg)

        self._channel_hash_to_cid.pop(ch.channel_hash, None)

        my_hash = getattr(self._destination, "hash", None)
        self.events.emit("leave", ch, self.nick, my_hash)
        log.info("Left %s%s", ch.name, "" if announce else " (silent)")

    def change_nick(self, new_nick: str, announce: bool = True):
        """
        Change nickname, update member lists, and optionally
        broadcast a NICK stanza to all joined channels.
        """
        old_nick = self.nick
        self.nick = new_nick
        my_hash = getattr(self._destination, "hash", None)

        for ch in self.channels.values():
            if my_hash:
                ch._member_nick_change(my_hash, new_nick)
            else:
                # Local mode fallback — re-key by nick
                ts = ch.members.pop(old_nick, None)
                ch.members[new_nick] = ts or time.time()

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
        """Dispatch a message to a channel via its hub."""
        channel._record(msg)

        # Tag as locally originated so event handlers can distinguish
        # self-echo from hub-relayed messages with the same nick.
        msg._local = True

        if msg.type not in (MessageType.JOIN, MessageType.LEAVE, MessageType.ANNOUNCE, MessageType.NICK):
            self.events.emit(msg.type, channel, msg)

        if self._router is not None and channel.hub_hash is not None:
            self._send_to_hub(channel, msg)

    def _send_to_hub(self, channel: Channel, msg: LXCFMessage):
        """Wrap an LXCFMessage in a Channel Envelope and send to the hub."""
        if self._router is None or channel.hub_hash is None:
            return

        msg_fields = msg.to_fields()
        custom_data = msg_fields[FIELD_CUSTOM_DATA]

        if channel.key is not None:
            custom_data = encrypt_custom_data(custom_data, channel.key)

        envelope = ChannelEnvelope(
            channel_hash=channel.channel_hash,
            source_hash=self._destination.hash,
            custom_type=msg_fields[FIELD_CUSTOM_TYPE],
            custom_data=custom_data,
        )

        # DEBUG: remove for production
        stanza_type = msg.type if hasattr(msg, "type") else "?"
        _dbg(f"[client-debug] _send_to_hub: type={stanza_type} ch={channel.channel_hash.hex()[:8]} src(our dest)={self._destination.hash.hex()} hub={channel.hub_hash.hex()}")

        try:
            import RNS
            import LXMF

            fields = envelope.to_fields()
            dest_hash = channel.hub_hash

            recipient_identity = RNS.Identity.recall(dest_hash)
            if recipient_identity is None:
                RNS.Transport.request_path(dest_hash)
                log.info("Path to hub %s unknown, requested — message queued", dest_hash.hex())
                # DEBUG: remove for production
                _dbg(f"[client-debug]   hub identity unknown, requested path")

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
                # DEBUG: remove for production
                _dbg(f"[client-debug]   hub identity recalled OK, dest={dest.hash.hex()}")
                lxm = LXMF.LXMessage(
                    dest,
                    self._destination,
                    "",
                    fields=fields,
                    desired_method=LXMF.LXMessage.DIRECT,
                )

            self._router.handle_outbound(lxm)
            # DEBUG: remove for production
            _dbg(f"[client-debug]   handle_outbound OK")
        except Exception as exc:
            log.error("LXMF send to hub failed: %s", exc)
            # DEBUG: remove for production
            _dbg(f"[client-debug]   SEND FAILED: {exc}")

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

    def _lxmf_send_direct(self, destination_hash: bytes, msg: LXCFMessage):
        """
        Send an LXMF message to a single destination by hash.

        Attempts DIRECT delivery (over a Reticulum link).  If the
        path is unknown LXMF will request it and retry automatically.
        """
        try:
            import RNS
            import LXMF

            recipient_identity = RNS.Identity.recall(destination_hash)
            if recipient_identity is None:
                RNS.Transport.request_path(destination_hash)
                log.info("Path to %s unknown, requested — message queued", destination_hash.hex())

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

            # DEBUG: remove for production
            src_hash = getattr(message, "source_hash", None)
            src_hex = src_hash.hex() if src_hash else "?"
            _dbg(f"[client-debug] _on_lxmf_delivery from {src_hex}, field_keys={[hex(k) if isinstance(k, int) else k for k in fields.keys()]}")

            # --- Channel Envelope path (hub-relayed messages) ---
            if ChannelEnvelope.is_envelope(fields):
                envelope = ChannelEnvelope.from_fields(fields)

                # DEBUG: remove for production
                stanza_type = envelope.custom_data.get("t") if isinstance(envelope.custom_data, dict) else "<encrypted>"
                _dbg(f"[client-debug]   envelope: ch={envelope.channel_hash.hex()[:8]} src={envelope.source_hash.hex()} type={stanza_type}")

                # Discard if no matching local channel
                cid = self._channel_hash_to_cid.get(envelope.channel_hash)
                if cid is None:
                    # DEBUG: remove for production
                    _dbg(f"[client-debug]   DROPPED: no local channel for ch={envelope.channel_hash.hex()[:8]}")
                    return

                # Suppress self-echo
                if self._destination and envelope.source_hash == self._destination.hash:
                    # DEBUG: remove for production
                    _dbg(f"[client-debug]   DROPPED: self-echo (our dest={self._destination.hash.hex()})")
                    return

                # Blocked sender check
                if envelope.source_hash and self.is_blocked(envelope.source_hash):
                    log.debug("Blocked envelope from %s", envelope.source_hash.hex())
                    return

                # Decrypt encrypted envelopes (private channels)
                channel = self.channels.get(cid)
                if channel is None:
                    # DEBUG: remove for production
                    _dbg(f"[client-debug]   DROPPED: channel object gone for cid={cid}")
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

                # DEBUG: remove for production
                _dbg(f"[client-debug]   ACCEPTED: type={msg.type} nick={msg.nick} body={msg.body[:40] if msg.body else ''}")

                self._dispatch_inbound(msg, source_hash=envelope.source_hash, target_channel=channel)
                return

            # --- Regular direct LXCF message path ---
            if not LXCFMessage.is_lxcf(fields):
                # DEBUG: remove for production
                _dbg(f"[client-debug]   NOT an LXCF message — ignoring")
                return

            msg = LXCFMessage.from_fields(fields)
            source_hash = getattr(message, "source_hash", None)

            if source_hash and self.is_blocked(source_hash):
                log.debug("Blocked message from %s", source_hash.hex())
                return

            self._dispatch_inbound(msg, source_hash=source_hash)
        except Exception as exc:
            log.warning("Failed to process inbound LXCF message: %s", exc)
            # DEBUG: remove for production
            import traceback
            traceback.print_exc()

    def _dispatch_inbound(self, msg: LXCFMessage, source_hash: bytes | None = None, target_channel: "Channel | None" = None):
        """Route an inbound LXCFMessage to the right event handlers."""

        def _resolve(name: str | None) -> "Channel | None":
            """Return target_channel if set, else first channel matching name."""
            if target_channel is not None:
                return target_channel
            if name is None:
                return None
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
            leaving_hash = source_hash
            if ch and not leaving_hash:
                leaving_hash = ch.member_hashes.get(msg.nick)
            if ch:
                ch._member_leave(msg.nick, source_hash=source_hash)
            self.events.emit("leave", ch, msg.nick, leaving_hash)

        elif msg.type == MessageType.NICK:
            ch = _resolve(msg.channel)
            if ch and source_hash:
                old_nick = ch._member_nick_change(source_hash, msg.nick, msg.timestamp)
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
