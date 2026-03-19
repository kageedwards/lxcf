#!/usr/bin/env python3
"""
lxcf_bridge — NDJSON stdio bridge for Portulus.

Reads commands from stdin, drives an lxcf.Client,
writes events/responses to stdout.
Stderr is reserved for logging.

Usage (spawned by Electron main.js)::

    python -m lxcf_bridge

Protocol:
    - Commands arrive on stdin as single-line JSON with an "action" field.
    - Responses go to stdout as single-line JSON with a "response" field.
    - Events go to stdout as single-line JSON with an "event" field.
    - All stdout lines are compact JSON (no embedded newlines).
    - stderr is used for Python logging output only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import traceback

from lxcf.hub_config import load_hubs, save_hubs, resolve_hub, add_bookmark, remove_bookmark

log = logging.getLogger("lxcf_bridge")


class Bridge:
    """
    NDJSON stdio bridge for Portulus.

    Reads commands from stdin, drives an lxcf.Client,
    writes events/responses to stdout.
    Stderr is reserved for logging.
    """

    def __init__(self):
        self.client = None
        self._lock = threading.Lock()
        self._hubs_data = {"hubs": {}}
        self._store_path = os.path.expanduser("~/.lxcf")

    # ------------------------------------------------------------------
    # Thread-safe stdout writers
    # ------------------------------------------------------------------

    def write_event(self, obj: dict) -> None:
        """Write a JSON event to stdout (thread-safe)."""
        line = json.dumps(obj, separators=(",", ":"))
        with self._lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def write_response(self, req_id: str, data: dict) -> None:
        """Write a response correlated to a request ID."""
        data["response"] = req_id
        line = json.dumps(data, separators=(",", ":"))
        with self._lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    # ------------------------------------------------------------------
    # Hub lookup helpers
    # ------------------------------------------------------------------

    def _find_hub_tag_by_dest(self, hub_hash: bytes) -> str | None:
        """Return the friendly tag for a hub destination hash, or None."""
        dest_hex = hub_hash.hex()
        for tag, hub in self._hubs_data.get("hubs", {}).items():
            if hub.get("destination") == dest_hex:
                return tag
        return None

    # ------------------------------------------------------------------
    # Event wiring
    # ------------------------------------------------------------------

    def _wire_events(self) -> None:
        """Connect lxcf.Client events to write_event() calls."""
        c = self.client

        @c.on_message
        def on_message(channel, msg):
            # Skip locally-sent messages (the renderer shows them immediately)
            if getattr(msg, "_local", False):
                return
            h = channel.member_hashes.get(msg.nick) if channel else None
            suffix = h.hex()[:8] if h else None
            self.write_event({
                "event": "message",
                "cid": channel._cid if channel else None,
                "nick": msg.nick,
                "body": msg.body,
                "timestamp": msg.timestamp,
                "suffix": suffix,
            })

        @c.on_join
        def on_join(channel, nick):
            our_hash = c._destination.hash if c._destination else None
            h = channel.member_hashes.get(nick) if channel else None
            if our_hash and h and h == our_hash:
                return
            h = channel.member_hashes.get(nick) if channel else None
            suffix = h.hex()[:8] if h else None
            self.write_event({
                "event": "join",
                "cid": channel._cid if channel else None,
                "nick": nick,
                "suffix": suffix,
            })
            self._send_members(channel)

        @c.on_leave
        def on_leave(channel, nick):
            self.write_event({
                "event": "leave",
                "cid": channel._cid if channel else None,
                "nick": nick,
            })
            self._send_members(channel)

        @c.on_privmsg
        def on_privmsg(source_hash, msg):
            suffix = source_hash.hex()[:8] if source_hash else "?"
            self.write_event({
                "event": "privmsg",
                "nick": msg.nick,
                "body": msg.body,
                "timestamp": msg.timestamp,
                "suffix": suffix,
            })

        c.events.on("nick", lambda old, new: self._on_nick(old, new))
        c.events.on("emote", lambda ch, msg: self._on_emote(ch, msg))
        c.events.on("topic", lambda ch, msg: self._on_topic(ch, msg))

    def _on_nick(self, old_nick: str, new_nick: str) -> None:
        self.write_event({
            "event": "nick",
            "old_nick": old_nick,
            "new_nick": new_nick,
        })
        # refresh members on all channels
        for ch in self.client.channels.values():
            self._send_members(ch)

    def _on_emote(self, channel, msg) -> None:
        if getattr(msg, "_local", False):
            return
        h = channel.member_hashes.get(msg.nick) if channel else None
        suffix = h.hex()[:8] if h else None
        self.write_event({
            "event": "emote",
            "cid": channel._cid if channel else None,
            "nick": msg.nick,
            "body": msg.body,
            "timestamp": msg.timestamp,
            "suffix": suffix,
        })

    def _on_topic(self, channel, msg) -> None:
        self.write_event({
            "event": "topic",
            "cid": channel._cid if channel else None,
            "nick": msg.nick,
            "body": msg.body,
        })

    def _send_members(self, channel) -> None:
        """Emit a members event for a channel."""
        if channel is None:
            return
        our_hash = self.client._destination.hash if self.client._destination else None
        members = []
        for nick in channel.members:
            h = channel.member_hashes.get(nick)
            members.append({
                "nick": nick,
                "suffix": h.hex()[:8] if h else None,
                "is_self": h is not None and our_hash is not None and h == our_hash,
            })
        self.write_event({
            "event": "members",
            "cid": channel._cid,
            "members": members,
        })

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def handle_init(self, msg: dict) -> None:
        """Initialize RNS, LXMF, lxcf.Client, wire events, emit 'ready'."""
        import RNS
        import LXMF
        from lxcf.client import Client

        nick = msg.get("nick", "anon")
        rns_config_dir = msg.get("rns_config_dir")

        # Expand ~ in paths
        import os
        if rns_config_dir:
            rns_config_dir = os.path.expanduser(rns_config_dir)

        store_path = os.path.expanduser("~/.lxcf")
        self._store_path = store_path
        identity_path = os.path.join(store_path, "identity")

        # Initialize Reticulum
        reticulum = RNS.Reticulum(configdir=rns_config_dir)

        # Load or create identity
        os.makedirs(store_path, exist_ok=True)
        if os.path.exists(identity_path):
            identity = RNS.Identity.from_file(identity_path)
            if identity is None:
                # File corrupt — create fresh
                identity = RNS.Identity()
                identity.to_file(identity_path)
        else:
            identity = RNS.Identity()
            identity.to_file(identity_path)

        # Create LXMF router
        router = LXMF.LXMRouter(identity=identity, storagepath=store_path)
        dest = router.register_delivery_identity(identity, display_name=nick)

        # Create LXCF client
        self.client = Client(router=router, destination=dest, nick=nick)
        self._wire_events()

        # Announce so the hub (and other nodes) can discover our identity
        router.announce(dest.hash)

        # Load hubs/bookmarks
        self._hubs_data = load_hubs(store_path)

        # Emit ready
        addr = self.client.address or ""
        suffix = addr[:8] if addr else ""
        self.write_event({
            "event": "ready",
            "nick": self.client.nick,
            "address": addr,
            "suffix": suffix,
            "hubs": self._hubs_data,
        })

    def handle_join(self, msg: dict) -> dict:
        """Join a channel, return {ok, cid, name, hub, destHash}."""
        channel_name = msg["channel"]
        hub_tag = msg.get("hub")
        key_hex = msg.get("key")

        # Resolve hub tag to destination hash bytes
        hub_hash = None
        resolved_tag = hub_tag  # the friendly tag to return to the renderer
        if hub_tag:
            hub_hash = resolve_hub(self._hubs_data, hub_tag)
            if hub_hash is None and hub_tag != "local":
                # Try interpreting the tag as a raw hex destination hash
                try:
                    raw = bytes.fromhex(hub_tag)
                    if len(raw) == 16:
                        hub_hash = raw
                        # Look up if any saved hub has this destination
                        resolved_tag = self._find_hub_tag_by_dest(hub_hash)
                except ValueError:
                    pass
            if hub_hash is None and hub_tag != "local":
                return {"ok": False, "error": f"Unknown hub: {hub_tag}"}

        # Convert key hex to bytes
        key = None
        if key_hex:
            try:
                key = bytes.fromhex(key_hex)
            except ValueError:
                # Fallback: hash non-hex strings for backward compat
                key = hashlib.sha256(key_hex.encode()).digest()

        ch = self.client.join(channel_name, key=key, hub=hub_hash)
        hub_dest = hub_hash.hex() if hub_hash else None
        self._send_members(ch)
        return {
            "ok": True,
            "cid": ch._cid,
            "name": ch.name,
            "hub": resolved_tag,
            "key": key_hex,
            "destHash": hub_dest,
        }

    def handle_leave(self, msg: dict) -> dict:
        """Leave a channel, return {ok}."""
        self.client.leave(msg["cid"])
        return {"ok": True}

    def handle_send(self, msg: dict) -> dict:
        """Send a message to a channel, return {ok}."""
        self.client.send(msg["cid"], msg["body"])
        return {"ok": True}

    def handle_emote(self, msg: dict) -> dict:
        """Send an emote to a channel, return {ok}."""
        ch = self.client.channels.get(msg["cid"])
        if ch is None:
            raise ValueError(f"Not in channel {msg['cid']}")
        ch.emote(msg["body"])
        return {"ok": True}

    def handle_set_topic(self, msg: dict) -> dict:
        """Set channel topic, return {ok}."""
        ch = self.client.channels.get(msg["cid"])
        if ch is None:
            raise ValueError(f"Not in channel {msg['cid']}")
        ch.set_topic(msg["topic"])
        return {"ok": True}

    def handle_change_nick(self, msg: dict) -> dict:
        """Change nick, return {ok, nick}."""
        self.client.change_nick(msg["nick"])
        # Member updates are pushed by the nick event handler
        return {"ok": True, "nick": self.client.nick}

    def handle_privmsg(self, msg: dict) -> dict:
        """Send a private message, return {ok}."""
        self.client.privmsg(msg["dest_hash"], msg["body"])
        return {"ok": True}

    def handle_get_hubs(self, msg: dict) -> dict:
        """Return current hubs/bookmarks data."""
        return {"ok": True, "hubs": self._hubs_data}

    def handle_save_hub(self, msg: dict) -> dict:
        """Add or update a hub entry, return updated hubs data."""
        tag = msg["tag"]
        destination = msg.get("destination")
        hubs = self._hubs_data.setdefault("hubs", {})
        if tag in hubs:
            hubs[tag]["destination"] = destination
        else:
            hubs[tag] = {"destination": destination, "channels": []}
        save_hubs(self._store_path, self._hubs_data)
        return {"ok": True, "hubs": self._hubs_data}

    def handle_delete_hub(self, msg: dict) -> dict:
        """Remove a hub entry, return updated hubs data."""
        tag = msg["tag"]
        self._hubs_data.get("hubs", {}).pop(tag, None)
        save_hubs(self._store_path, self._hubs_data)
        return {"ok": True, "hubs": self._hubs_data}

    def handle_toggle_bookmark(self, msg: dict) -> dict:
        """Add or remove a bookmark under a hub, return updated hubs data."""
        channel_name = msg["channel"]
        hub_tag = msg.get("hub")
        key_hex = msg.get("key")

        # If hub_tag is a raw hex destination hash (not a saved tag name),
        # look up the friendly tag for it.
        if hub_tag and hub_tag not in self._hubs_data.get("hubs", {}):
            try:
                raw = bytes.fromhex(hub_tag)
                if len(raw) == 16:
                    found = self._find_hub_tag_by_dest(raw)
                    if found:
                        hub_tag = found
            except ValueError:
                pass

        if not hub_tag:
            hub_tag = "local"

        # Check if bookmark exists
        hub = self._hubs_data.get("hubs", {}).get(hub_tag, {})
        channels = hub.get("channels", [])
        exists = any(
            ch["name"] == channel_name and ch.get("key") == key_hex
            for ch in channels
        )

        if exists:
            remove_bookmark(self._hubs_data, hub_tag, channel_name, key_hex)
        else:
            add_bookmark(self._hubs_data, hub_tag, channel_name, key_hex)

        save_hubs(self._store_path, self._hubs_data)
        return {"ok": True, "hubs": self._hubs_data}

    def handle_quit(self, msg: dict, req_id: str | None = None) -> None:
        """Leave all channels, write final response, sys.exit(0)."""
        for cid in list(self.client.channels.keys()):
            self.client.leave(cid)
        if req_id:
            self.write_response(req_id, {"ok": True})
        sys.exit(0)

    # ------------------------------------------------------------------
    # Action dispatch
    # ------------------------------------------------------------------

    HANDLERS = {
        "join": "handle_join",
        "leave": "handle_leave",
        "send": "handle_send",
        "emote": "handle_emote",
        "set_topic": "handle_set_topic",
        "change_nick": "handle_change_nick",
        "privmsg": "handle_privmsg",
        "get_hubs": "handle_get_hubs",
        "save_hub": "handle_save_hub",
        "delete_hub": "handle_delete_hub",
        "toggle_bookmark": "handle_toggle_bookmark",
    }

    def _dispatch(self, msg: dict) -> None:
        """Route a parsed command to the appropriate handler."""
        action = msg.get("action")
        req_id = msg.get("id")

        if action == "init":
            try:
                self.handle_init(msg)
            except Exception as exc:
                log.error("init failed: %s", exc)
                traceback.print_exc(file=sys.stderr)
                if req_id:
                    self.write_response(req_id, {"ok": False, "error": str(exc)})
            return

        if action == "quit":
            self.handle_quit(msg, req_id=req_id)
            return

        handler_name = self.HANDLERS.get(action)
        if handler_name is None:
            resp = {"ok": False, "error": f"unknown action: {action}"}
            if req_id:
                self.write_response(req_id, resp)
            return

        try:
            result = getattr(self, handler_name)(msg)
            if req_id:
                self.write_response(req_id, result)
        except Exception as exc:
            log.error("%s failed: %s", action, exc)
            traceback.print_exc(file=sys.stderr)
            if req_id:
                self.write_response(req_id, {"ok": False, "error": str(exc)})

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Read NDJSON commands from stdin in a blocking loop."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.warning("Bad JSON on stdin: %s", line)
                continue
            self._dispatch(msg)


def main():
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="[bridge] %(levelname)s %(message)s",
    )
    bridge = Bridge()
    bridge.run()


if __name__ == "__main__":
    main()
