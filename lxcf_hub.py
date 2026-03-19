#!/usr/bin/env python3
"""
lxcf_hub — LXCF Relay Hub daemon.

Starts a relay hub that accepts channel subscriptions and fans out
messages to subscribers via LXMF SINGLE delivery.

Usage::

    python -m lxcf_hub
    python -m lxcf_hub --store ~/.lxcf-hub --max-channels 64
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time


def main():
    parser = argparse.ArgumentParser(description="LXCF Relay Hub daemon")
    parser.add_argument("--store", default="~/.lxcf-hub", help="Storage path (default: ~/.lxcf-hub)")
    parser.add_argument("--identity", default=None, help="Path to existing RNS identity file")
    parser.add_argument("--max-channels", type=int, default=32, help="Max channels (default: 32)")
    parser.add_argument("--max-subscribers", type=int, default=32, help="Max subscribers per channel (default: 32)")
    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="[hub] %(levelname)s %(message)s",
    )
    log = logging.getLogger("lxcf_hub")

    import RNS
    import LXMF
    from lxcf.hub import Hub

    store = os.path.expanduser(args.store)
    os.makedirs(store, exist_ok=True)

    log.info("Starting Reticulum...")
    RNS.Reticulum()

    # Load or create identity
    id_path = os.path.join(store, "identity")
    if args.identity:
        identity = RNS.Identity.from_file(os.path.expanduser(args.identity))
        if identity is None:
            identity = RNS.Identity()
            identity.to_file(os.path.expanduser(args.identity))
            log.info("Created new identity at %s", args.identity)
        else:
            log.info("Loaded identity from %s", args.identity)
    elif os.path.isfile(id_path):
        identity = RNS.Identity.from_file(id_path)
        if identity is None:
            identity = RNS.Identity()
            identity.to_file(id_path)
            log.info("Identity file corrupt, created new identity at %s", id_path)
        else:
            log.info("Loaded identity from %s", id_path)
    else:
        identity = RNS.Identity()
        identity.to_file(id_path)
        log.info("Created new identity at %s", id_path)

    router = LXMF.LXMRouter(identity=identity, storagepath=store)

    hub = Hub(
        router=router,
        identity=identity,
        max_channels=args.max_channels,
        max_subscribers_per_channel=args.max_subscribers,
    )

    router.announce(hub.destination_hash)
    dest_hex = hub.destination_hash.hex()
    print(dest_hex, flush=True)
    log.info("Hub running — destination: %s", dest_hex)
    log.info("Max channels: %d, max subscribers/channel: %d", args.max_channels, args.max_subscribers)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
