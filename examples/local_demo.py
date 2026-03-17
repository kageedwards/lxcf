#!/usr/bin/env python3
"""
Minimal LXCF demo — runs entirely in local mode (no Reticulum needed).

Shows how the Client, Channel, and EventBus fit together.
"""

import lxcf
from lxcf.util import format_irc_style


def main():
    # Create two local clients (no LXMF router -> local-only mode)
    alice = lxcf.Client(nick="alice")
    bob   = lxcf.Client(nick="bob")

    # Bob listens for everything interesting
    @bob.on_message
    def on_msg(channel, msg):
        print(format_irc_style(msg))

    @bob.on_join
    def on_join(channel, nick):
        print(f"  --> {nick} joined {channel}")

    @bob.events.on("topic")
    def on_topic(channel, msg):
        print(format_irc_style(msg))

    @bob.events.on("emote")
    def on_emote(channel, msg):
        print(format_irc_style(msg))

    # Alice joins #mesh and sends some messages
    ch = alice.join("#mesh")
    ch.send("Hello from the mesh!")
    ch.emote("waves")
    ch.set_topic("Off-grid comms")

    # Simulate bob receiving those messages
    print("--- Bob's view ---")
    for msg in ch.history:
        bob._dispatch_inbound(msg)

    # Direct message
    print("\n--- Direct message ---")
    dm = alice.privmsg(b"\x00" * 16, "Hey bob, private message here")
    print(format_irc_style(dm))

    # Serialisation round-trip through LXMF fields
    print("\n--- Round-trip test ---")
    original = lxcf.LXCFMessage.chat("kage", "#reticulum", "msgpack is tiny")
    fields = original.to_fields()
    restored = lxcf.LXCFMessage.from_fields(fields)
    print(f"Original:  {original}")
    print(f"Restored:  {restored}")
    print(f"Fields:    {fields}")
    print(f"is_lxcf:   {lxcf.LXCFMessage.is_lxcf(fields)}")
    print(f"is_lxcf({{}}): {lxcf.LXCFMessage.is_lxcf({})}")


if __name__ == "__main__":
    main()
