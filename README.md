# LXCF

Lightweight eXtensible CHANNEL Format — an IRC-style semantic layer / protocol over [LXMF](https://github.com/markqvist/LXMF) and [Reticulum](https://github.com/markqvist/Reticulum).

### Multi-hop channel messaging via relay hubs. Private channels, deterministic addressing, and all 8 stanza types work across the full Reticulum mesh.

LXCF embeds structured stanzas inside standard LXMF messages using `FIELD_CUSTOM_TYPE` / `FIELD_CUSTOM_DATA`, so all traffic is transparent to existing LXMF clients and propagation nodes.

## Protocol Stack

```
LXCF  — channels, nicks, topics, emotes, DMs
LXMF  — message routing, delivery, propagation
RNS   — mesh networking, cryptographic identities
```

## Features

- 8 stanza types: message, privmsg, join, leave, nick, topic, emote, announce
- Relay hub model for multi-hop channel messaging across the mesh
- Channels identified by deterministic SHA-256 hashes (16 bytes)
- Private channels via symmetric key encryption (hub cannot read payload)
- Per-channel hub association — join channels on different hubs simultaneously
- Local-only mode for testing without a mesh stack
- Lightweight event bus for pub/sub

## Install

```bash
pip install -e .
```

Requires Python ≥ 3.10. RNS and LXMF are installed as dependencies but imported lazily — the library can be used without the underlying stack for testing.

## Quick Start

### Local Testing (no network stack)

```python
import lxcf

alice = lxcf.Client(nick="alice")
bob = lxcf.Client(nick="bob")

ch = alice.join("#mesh")
bob.join("#mesh")

bob.onMessage(lambda channel, msg: print(f"<{msg.nick}> {msg.body}"))
ch.send("hello from alice")
```

### Over LXMF

```python
import RNS, LXMF, lxcf

reticulum = RNS.Reticulum()
identity = RNS.Identity()
router = LXMF.LXMRouter(identity=identity, storagepath="./store")
dest = router.register_delivery_identity(identity, display_name="alice")

client = lxcf.Client(router=router, destination=dest, nick="alice")
ch = client.join("#mesh")
ch.send("hello mesh")
```

### Via Relay Hub

```python
import RNS, LXMF, lxcf

reticulum = RNS.Reticulum()
identity = RNS.Identity()
router = LXMF.LXMRouter(identity=identity, storagepath="./store")
dest = router.register_delivery_identity(identity, display_name="alice")

client = lxcf.Client(router=router, destination=dest, nick="alice")

# Join a channel through a relay hub (multi-hop)
hub_hash = bytes.fromhex("abcdef0123456789abcdef0123456789")
ch = client.join("#mesh", hub=hub_hash)
ch.send("hello mesh — relayed across the network")
```

### Running a Hub

```python
import RNS, LXMF
from lxcf.hub import Hub

reticulum = RNS.Reticulum()
identity = RNS.Identity()
router = LXMF.LXMRouter(identity=identity, storagepath="./hub_store")

hub = Hub(router=router, identity=identity)
print(f"Hub running: {hub.destination_hash.hex()}")
# Hub now accepts subscriptions and relays channel messages
```

Or use the standalone daemon:

```bash
python -m lxcf_hub --store ~/.lxcf-hub --max-channels 64
```

## Examples

- `examples/local_demo.py` — two clients chatting locally, no network needed
- `examples/lxmf_demo.py` — interactive mesh chat over LXMF

## Project Structure

```
lxcf/
  protocol.py    — constants, MessageType enum, LXMF field IDs, Channel_Hash derivation
  message.py     — LXCFMessage serialization
  channel.py     — Channel state (members, topic, history, hub association)
  client.py      — Client API, LXMF integration, hub-aware routing, event dispatch
  envelope.py    — ChannelEnvelope for hub relay, private channel encryption
  hub.py         — Relay Hub: subscription registry, message fan-out
  events.py      — EventBus pub/sub
  util.py        — nick formatting, deduplication
```

## See also

- [Portulus](https://github.com/kageedwards/portulus) — desktop client (Electron)

## License

MIT
