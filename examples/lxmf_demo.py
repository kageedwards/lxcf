#!/usr/bin/env python3
"""
LXCF over LXMF — real mesh messaging example.

Requires: pip install rns lxmf

This creates a Reticulum instance, an LXMF router, and an LXCF
client wired into it.  Channel messages use GROUP destinations
(AES-128 symmetric encryption), direct messages use SINGLE
destinations (Curve25519 ECDH).

Run two instances in separate terminals to chat:

    Terminal 1:  python examples/lxmf_demo.py --nick alice
    Terminal 2:  python examples/lxmf_demo.py --nick bob

Each instance prints its destination hash on startup.  Use /dm
to send direct messages, or just type to chat on #mesh.
"""

import argparse
import os
import sys


def _default_store():
    return os.path.join(os.path.expanduser("~"), ".lxcf")


def main():
    parser = argparse.ArgumentParser(description="LXCF over LXMF demo")
    parser.add_argument("--nick", default="anon", help="Display name")
    parser.add_argument("--store", default=_default_store(), help="LXMF storage dir")
    parser.add_argument("--channel", default="#mesh", help="Default channel to join")
    parser.add_argument("--identity", default=None, help="Path to an existing RNS identity file")
    args = parser.parse_args()

    # Imports that start the Reticulum stack — kept inside main()
    # so the module stays importable without RNS installed.
    import RNS
    import LXMF
    import lxcf
    from lxcf.util import format_irc_style

    os.makedirs(args.store, exist_ok=True)

    # 1. Boot Reticulum + LXMF router
    reticulum = RNS.Reticulum()

    if args.identity:
        identity = RNS.Identity.from_file(args.identity)
    else:
        id_path = os.path.join(args.store, "identity")
        if os.path.isfile(id_path):
            identity = RNS.Identity.from_file(id_path)
        else:
            identity = RNS.Identity()
            identity.to_file(id_path)

    router    = LXMF.LXMRouter(identity=identity, storagepath=args.store)
    dest      = router.register_delivery_identity(identity, display_name=args.nick)

    # 2. Create the LXCF client — it registers its delivery callback
    #    on the router automatically.
    client = lxcf.Client(router=router, destination=dest, nick=args.nick)

    # 3. Wire up event handlers
    @client.on_message
    def on_msg(channel, msg):
        print(format_irc_style(msg))

    @client.on_privmsg
    def on_dm(source_hash, msg):
        src = source_hash.hex()[:8] if source_hash else "unknown"
        print(f"[DM from {src}] <{msg.nick}> {msg.body}")

    @client.on_join
    def on_join(channel, nick):
        if nick != client.nick:
            print(f"  --> {nick} joined {channel}")

    @client.events.on("emote")
    def on_emote(channel, msg):
        print(format_irc_style(msg))

    @client.events.on("topic")
    def on_topic(channel, msg):
        print(format_irc_style(msg))

    # 4. Announce so others can find us
    router.announce(dest.hash)

    # 5. Join default channel (creates a GROUP destination)
    ch = client.join(args.channel)

    print(f"LXCF ready — {client.nick} on {args.channel}")
    print(f"Address: {dest.hash.hex()}")
    if ch.destination:
        print(f"Channel group hash: {ch.destination.hexhash}")
    print()
    print("Commands: /dm <hash> <msg> | /me <action> | /topic <text> | /nick <name> | /quit")
    print()

    # 6. Input loop
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            print("\nLeaving.")
            break

        if not line.strip():
            continue

        if line.startswith("/dm "):
            parts = line.split(" ", 2)
            if len(parts) < 3:
                print("Usage: /dm <destination_hash> <message>")
                continue
            client.privmsg(parts[1], parts[2])

        elif line.startswith("/me "):
            ch.emote(line[4:])

        elif line.startswith("/topic "):
            ch.set_topic(line[7:])

        elif line.startswith("/nick "):
            client.nick = line[6:].strip()
            print(f"Nick changed to {client.nick}")

        elif line.strip() == "/quit":
            client.leave(args.channel)
            break

        else:
            ch.send(line)


if __name__ == "__main__":
    main()
