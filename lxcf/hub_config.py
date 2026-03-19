"""
LXCF Hub Configuration — shared hub + bookmark I/O.

Both the bridge and TUI use this module to read/write the unified
``~/.lxcf/bookmarks.json`` file, which stores hub definitions
(tag + destination hash) with nested channel bookmarks.

Format::

    {
      "hubs": {
        "rmap": {
          "destination": "a3f8b2c1...",
          "channels": [
            {"name": "#mesh"},
            {"name": "#private", "key": "ab01cd02..."}
          ]
        }
      }
    }
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("lxcf")

_EMPTY = {"hubs": {}}


def bookmarks_path(store_path: str) -> str:
    """Return the path to bookmarks.json."""
    return os.path.join(store_path, "bookmarks.json")


def load_hubs(store_path: str) -> dict:
    """Load bookmarks.json. Returns ``{"hubs": {}}`` if missing or corrupt."""
    path = bookmarks_path(store_path)
    if not os.path.isfile(path):
        return {"hubs": {}}
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict) or "hubs" not in data:
            return {"hubs": {}}
        return data
    except Exception:
        log.warning("Failed to parse %s, using empty default", path)
        return {"hubs": {}}


def save_hubs(store_path: str, data: dict) -> None:
    """Write bookmarks.json."""
    path = bookmarks_path(store_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def resolve_hub(data: dict, tag: str) -> bytes | None:
    """
    Resolve a hub tag to a 16-byte destination hash, or None.

    Returns None if the tag is unknown, the destination is null,
    or the hex string is invalid.
    """
    hub = data.get("hubs", {}).get(tag)
    if hub is None:
        return None
    dest = hub.get("destination")
    if not dest:
        return None
    try:
        return bytes.fromhex(dest)
    except (ValueError, TypeError):
        return None


def add_bookmark(data: dict, hub_tag: str, channel_name: str, key_hex: str | None = None) -> dict:
    """
    Add a channel bookmark under a hub. Creates the hub entry if needed.

    For the special ``"local"`` tag, the hub destination is set to null.
    Returns the modified data dict.
    """
    hubs = data.setdefault("hubs", {})
    if hub_tag not in hubs:
        hubs[hub_tag] = {
            "destination": None if hub_tag == "local" else "",
            "channels": [],
        }
    channels = hubs[hub_tag].setdefault("channels", [])

    # Check for duplicate
    for ch in channels:
        if ch["name"] == channel_name and ch.get("key") == key_hex:
            return data

    entry: dict = {"name": channel_name}
    if key_hex:
        entry["key"] = key_hex
    channels.append(entry)
    return data


def remove_bookmark(data: dict, hub_tag: str, channel_name: str, key_hex: str | None = None) -> dict:
    """
    Remove a channel bookmark from a hub.

    Returns the modified data dict. No-op if not found.
    """
    hub = data.get("hubs", {}).get(hub_tag)
    if hub is None:
        return data
    channels = hub.get("channels", [])
    hub["channels"] = [
        ch for ch in channels
        if not (ch["name"] == channel_name and ch.get("key") == key_hex)
    ]
    return data


def update_hub(data: dict, hub_tag: str, label: str | None = None, destination: str | None = None) -> dict:
    """
    Update a hub's label and/or destination hash.

    Only non-None values are written. Creates the hub entry if it doesn't
    exist. Returns the modified data dict.
    """
    hubs = data.setdefault("hubs", {})
    if hub_tag not in hubs:
        hubs[hub_tag] = {"destination": None, "channels": []}
    hub = hubs[hub_tag]
    if label is not None:
        if label.strip():
            hub["label"] = label.strip()
        else:
            hub.pop("label", None)
    if destination is not None:
        hub["destination"] = destination.strip() if destination.strip() else None
    return data
