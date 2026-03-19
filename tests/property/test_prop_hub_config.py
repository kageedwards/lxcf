"""
Property tests for lxcf.hub_config — hub + bookmark I/O.

Feature: hub-config-and-routing
"""

import json
import os
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from lxcf.hub_config import load_hubs, save_hubs, resolve_hub, add_bookmark, remove_bookmark


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

hub_tags = st.text(alphabet=string.ascii_lowercase + string.digits + "-", min_size=1, max_size=20)
channel_names = st.text(alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=20).map(lambda s: f"#{s}")
dest_hexes = st.binary(min_size=16, max_size=16).map(lambda b: b.hex())
key_hexes = st.one_of(st.none(), st.binary(min_size=32, max_size=32).map(lambda b: b.hex()))


@st.composite
def hubs_data(draw):
    """Generate a valid hubs dict with 0-5 hubs, each with 0-5 channels."""
    n_hubs = draw(st.integers(min_value=0, max_value=5))
    hubs = {}
    for _ in range(n_hubs):
        tag = draw(hub_tags)
        dest = draw(dest_hexes)
        n_ch = draw(st.integers(min_value=0, max_value=5))
        channels = []
        for _ in range(n_ch):
            entry = {"name": draw(channel_names)}
            k = draw(key_hexes)
            if k:
                entry["key"] = k
            channels.append(entry)
        hubs[tag] = {"destination": dest, "channels": channels}
    return {"hubs": hubs}


# ---------------------------------------------------------------------------
# Property: load/save round-trip
# ---------------------------------------------------------------------------

@given(data=hubs_data())
@settings(max_examples=200)
def test_load_save_roundtrip(data, tmp_path_factory):
    """
    Feature: hub-config-and-routing
    Property: load/save round-trip — save then load produces identical data.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as store:
        save_hubs(store, data)
        loaded = load_hubs(store)
        assert loaded == data


# ---------------------------------------------------------------------------
# Property: resolve_hub returns correct bytes for known tags
# ---------------------------------------------------------------------------

@given(data=hubs_data())
@settings(max_examples=200)
def test_resolve_hub_correctness(data):
    """
    Feature: hub-config-and-routing
    Property: resolve_hub returns correct bytes for every tag with a valid destination.
    """
    for tag, hub in data["hubs"].items():
        dest = hub.get("destination")
        result = resolve_hub(data, tag)
        if dest:
            assert result == bytes.fromhex(dest)
        else:
            assert result is None


# ---------------------------------------------------------------------------
# Property: add then remove bookmark is identity
# ---------------------------------------------------------------------------

@given(data=hubs_data(), tag=hub_tags, name=channel_names, key=key_hexes)
@settings(max_examples=200)
def test_add_remove_bookmark_identity(data, tag, name, key):
    """
    Feature: hub-config-and-routing
    Property: adding a NEW bookmark then removing it returns to original channel list.
    """
    import copy

    # Only test when the bookmark doesn't already exist (otherwise add is no-op
    # but remove still deletes, which is correct but not an identity).
    existing = data.get("hubs", {}).get(tag, {}).get("channels", [])
    already_exists = any(
        ch["name"] == name and ch.get("key") == key for ch in existing
    )
    if already_exists:
        return  # skip — not a meaningful identity test case

    original = copy.deepcopy(data)

    # Capture original channels for this hub (if any)
    orig_channels = []
    if tag in original.get("hubs", {}):
        orig_channels = list(original["hubs"][tag].get("channels", []))

    # Add then remove
    add_bookmark(data, tag, name, key)
    remove_bookmark(data, tag, name, key)

    # The channels list for this hub should match original
    if tag in data.get("hubs", {}):
        result_channels = data["hubs"][tag].get("channels", [])
        assert result_channels == orig_channels
