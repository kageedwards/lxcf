# LXCF

Lightweight eXtensible CHANNEL Format — an IRC-style semantic layer / protocol over [LXMF](https://github.com/markqvist/LXMF) and [Reticulum](https://github.com/markqvist/Reticulum).

### Major work-in-progress right now.

LXCF embeds structured stanzas inside standard LXMF messages using `FIELD_CUSTOM_TYPE` / `FIELD_CUSTOM_DATA`, so all traffic is transparent to existing LXMF clients and propagation nodes.

## Protocol Stack

```
LXCF  — channels, nicks, topics, emotes, DMs
LXMF  — message routing, delivery, propagation
RNS   — mesh networking, cryptographic identities
```

## Features

- 8 stanza types: message, privmsg, join, leave, nick, topic, emote, announce
- Channels map to deterministic RNS GROUP destinations
- Private channels via shared subnet passphrases (SHA-256 derived symmetric keys)
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

## Examples

- `examples/local_demo.py` — two clients chatting locally, no network needed
- `examples/lxmf_demo.py` — interactive mesh chat over LXMF

## Project Structure

```
lxcf/
  protocol.py    — constants, MessageType enum, LXMF field IDs
  message.py     — LXCFMessage serialization
  channel.py     — Channel state (members, topic, history)
  client.py      — Client API, LXMF integration, event dispatch
  events.py      — EventBus pub/sub
  util.py        — nick formatting, deduplication
```

## See also

- [Portulus](https://github.com/kageedwards/portulus) — desktop client (Electron)

## License

MIT
