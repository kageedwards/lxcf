"""
LXCF Relay Hub — accepts channel subscriptions and relays messages.

The Hub registers an LXMF delivery identity and acts as a relay node
for channel traffic.  Clients send Channel Envelopes to the Hub, which
maintains a subscription registry and fans out each message to all
other subscribers of the target channel.

The Hub is stanza-agnostic: it relays any valid Channel Envelope
regardless of the enclosed stanza type.  JOIN and LEAVE stanzas have
the additional side effect of updating the subscription registry.

Like the Client, the Hub never imports RNS/LXMF at module level —
all mesh-stack imports happen lazily inside method bodies.
"""

from __future__ import annotations

import logging

from lxcf.envelope import ChannelEnvelope
from lxcf.protocol import MessageType

log = logging.getLogger("lxcf")


class Hub:
    """
    LXCF Relay Hub — accepts channel subscriptions and relays messages.

    Parameters
    ----------
    router : LXMF.LXMRouter
        An initialised LXMRouter.
    identity : RNS.Identity
        The identity to register for LXMF delivery.
    max_channels : int
        Maximum number of channels (default 32).
    max_subscribers_per_channel : int
        Maximum subscribers per channel (default 32).
    """

    def __init__(
        self,
        router,
        identity,
        max_channels: int = 32,
        max_subscribers_per_channel: int = 32,
    ):
        self._router = router
        self._identity = identity
        self._max_channels = max_channels
        self._max_subscribers_per_channel = max_subscribers_per_channel

        # Subscription registry: Channel_Hash -> set of subscriber dest hashes
        self._subscriptions: dict[bytes, set[bytes]] = {}

        # Register the LXMF delivery identity and wire up the callback
        self._destination = router.register_delivery_identity(
            identity, display_name="lxcf-hub",
        )
        self._router.register_delivery_callback(self._on_lxmf_delivery)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def destination_hash(self) -> bytes:
        """The Hub's LXMF delivery destination hash."""
        return self._destination.hash

    # ------------------------------------------------------------------
    # Inbound handling
    # ------------------------------------------------------------------

    def _on_lxmf_delivery(self, message) -> None:
        """
        LXMF delivery callback — dispatches inbound Channel Envelopes.

        Non-envelope messages are silently ignored.  Envelopes with
        malformed Channel_Hash (not 16 bytes) are discarded with a
        warning.
        """
        fields = message.fields if hasattr(message, "fields") else {}

        # DEBUG: remove for production
        src_hash = getattr(message, "source_hash", None)
        src_hex = src_hash.hex() if src_hash else "?"
        print(f"[hub-debug] LXMF delivery received from {src_hex} ({len(src_hash) if src_hash else 0} bytes), fields keys: {[hex(k) if isinstance(k, int) else k for k in fields.keys()]}", flush=True)

        if not ChannelEnvelope.is_envelope(fields):
            # DEBUG: remove for production
            print(f"[hub-debug] NOT an envelope — ignoring (no FIELD_CHANNEL_HASH in fields)", flush=True)
            return

        try:
            envelope = ChannelEnvelope.from_fields(fields)
        except ValueError as exc:
            log.warning("Hub: discarding malformed envelope: %s", exc)
            # DEBUG: remove for production
            print(f"[hub-debug] Malformed envelope: {exc}", flush=True)
            return

        if not isinstance(envelope.channel_hash, bytes) or len(envelope.channel_hash) != 16:
            log.warning(
                "Hub: discarding envelope with malformed Channel_Hash "
                "(expected 16 bytes, got %d)",
                len(envelope.channel_hash) if isinstance(envelope.channel_hash, bytes) else 0,
            )
            # DEBUG: remove for production
            print(f"[hub-debug] Bad Channel_Hash length — discarding", flush=True)
            return

        # DEBUG: remove for production
        stanza_type = envelope.custom_data.get("t") if isinstance(envelope.custom_data, dict) else "<encrypted>"
        print(f"[hub-debug] Valid envelope: ch={envelope.channel_hash.hex()} ({len(envelope.channel_hash)}B) src={envelope.source_hash.hex()} ({len(envelope.source_hash)}B) type={stanza_type}", flush=True)

        self._handle_envelope(envelope, message)

    def _handle_envelope(self, envelope: ChannelEnvelope, lxmf_message) -> None:
        """
        Process a validated Channel Envelope: update subscriptions, relay.

        For JOIN stanzas: add sender to subscriber set, then relay to
        existing subscribers.

        For LEAVE stanzas: relay to remaining subscribers, then remove
        sender from subscriber set.

        All other stanza types: auto-subscribe the sender if not already
        subscribed (implicit join), then relay to other subscribers.
        """
        ch = envelope.channel_hash
        src = envelope.source_hash

        # Determine stanza type if the custom_data is a dict (open channel)
        stanza_type = None
        if isinstance(envelope.custom_data, dict):
            stanza_type = envelope.custom_data.get("t")

        if stanza_type == MessageType.JOIN:
            # Check capacity limits before adding
            if ch not in self._subscriptions:
                if len(self._subscriptions) >= self._max_channels:
                    log.warning(
                        "Hub: channel limit reached (%d), discarding JOIN for %s",
                        self._max_channels, ch.hex()[:8],
                    )
                    return
                self._subscriptions[ch] = set()

            subs = self._subscriptions[ch]
            if src not in subs and len(subs) >= self._max_subscribers_per_channel:
                log.warning(
                    "Hub: subscriber limit reached (%d) for channel %s, "
                    "discarding JOIN from %s",
                    self._max_subscribers_per_channel, ch.hex()[:8], src.hex()[:8],
                )
                return

            # Add sender, then relay JOIN to existing subscribers
            subs.add(src)
            # DEBUG: remove for production
            print(f"[hub-debug] JOIN: added {src.hex()} ({len(src)}B) to ch={ch.hex()[:8]}, subscribers now: {[s.hex() for s in subs]}", flush=True)
            self._relay(ch, envelope, exclude=src)

        elif stanza_type == MessageType.LEAVE:
            # Relay LEAVE to remaining subscribers, then remove sender
            # DEBUG: remove for production
            print(f"[hub-debug] LEAVE: {src.hex()} from ch={ch.hex()[:8]}", flush=True)
            self._relay(ch, envelope, exclude=src)
            subs = self._subscriptions.get(ch)
            if subs is not None:
                subs.discard(src)
                if not subs:
                    del self._subscriptions[ch]

        else:
            # Auto-subscribe: if sender isn't subscribed, treat as implicit join
            if ch not in self._subscriptions:
                if len(self._subscriptions) >= self._max_channels:
                    log.warning(
                        "Hub: channel limit reached (%d), discarding message for %s",
                        self._max_channels, ch.hex()[:8],
                    )
                    return
                self._subscriptions[ch] = set()

            subs = self._subscriptions[ch]
            if src not in subs:
                if len(subs) >= self._max_subscribers_per_channel:
                    log.warning(
                        "Hub: subscriber limit reached (%d) for channel %s, "
                        "discarding message from %s",
                        self._max_subscribers_per_channel, ch.hex()[:8], src.hex()[:8],
                    )
                    return
                subs.add(src)
                # DEBUG: remove for production
                print(f"[hub-debug] AUTO-JOIN: added {src.hex()} to ch={ch.hex()[:8]} on first message", flush=True)

            targets = subs - {src}
            # DEBUG: remove for production
            print(f"[hub-debug] RELAY type={stanza_type}: from {src.hex()} on ch={ch.hex()[:8]}, relaying to {[t.hex() for t in targets]} (total subs: {[s.hex() for s in subs]})", flush=True)
            self._relay(ch, envelope, exclude=src)

    def _relay(self, channel_hash: bytes, envelope: ChannelEnvelope, exclude: bytes) -> None:
        """
        Fan-out: send envelope to all subscribers except *exclude*.

        Each subscriber receives an individual LXMF SINGLE message
        containing the envelope's fields.  The original sender's
        identity hash is preserved in the ``FIELD_SOURCE_HASH`` field.

        Silently returns if no subscribers exist for the channel.
        """
        subs = self._subscriptions.get(channel_hash)
        if not subs:
            # DEBUG: remove for production
            print(f"[hub-debug] _relay: no subscribers for ch={channel_hash.hex()[:8]} — nothing to send", flush=True)
            return

        targets = subs - {exclude}
        if not targets:
            # DEBUG: remove for production
            print(f"[hub-debug] _relay: all subscribers excluded for ch={channel_hash.hex()[:8]} (only sender subscribed)", flush=True)
            return

        import RNS
        import LXMF

        fields = envelope.to_fields()

        for dest_hash in targets:
            try:
                recipient_identity = RNS.Identity.recall(dest_hash)
                # DEBUG: remove for production
                print(f"[hub-debug]   -> sending to {dest_hash.hex()} ({len(dest_hash)}B), identity recalled: {recipient_identity is not None}", flush=True)
                if recipient_identity is None:
                    RNS.Transport.request_path(dest_hash)
                    # DEBUG: remove for production
                    print(f"[hub-debug]   -> identity unknown for {dest_hash.hex()}, requested path (message may not deliver)", flush=True)
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
                # DEBUG: remove for production
                print(f"[hub-debug]   -> handle_outbound called OK for {dest_hash.hex()}", flush=True)
            except Exception as exc:
                log.error(
                    "Hub: relay to %s failed: %s", dest_hash.hex()[:8], exc,
                )
                # DEBUG: remove for production
                print(f"[hub-debug]   -> RELAY FAILED to {dest_hash.hex()}: {exc}", flush=True)

    def __repr__(self) -> str:
        n_channels = len(self._subscriptions)
        n_subs = sum(len(s) for s in self._subscriptions.values())
        return (
            f"<Hub dest={self.destination_hash.hex()[:8]}… "
            f"channels={n_channels} subscribers={n_subs}>"
        )
