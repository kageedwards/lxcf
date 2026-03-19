"""
Unit tests for lxcf.hub_config — hub + bookmark I/O.
"""

import json
import os

from lxcf.hub_config import load_hubs, save_hubs, resolve_hub, add_bookmark, remove_bookmark, bookmarks_path


def test_load_missing_file_returns_empty(tmp_path):
    """Missing bookmarks.json returns empty default."""
    data = load_hubs(str(tmp_path))
    assert data == {"hubs": {}}


def test_resolve_unknown_tag_returns_none():
    """Resolving an unknown hub tag returns None."""
    data = {"hubs": {"rmap": {"destination": "aa" * 16, "channels": []}}}
    assert resolve_hub(data, "unknown") is None


def test_resolve_null_destination_returns_none():
    """Resolving a hub with null destination returns None."""
    data = {"hubs": {"local": {"destination": None, "channels": []}}}
    assert resolve_hub(data, "local") is None


def test_add_bookmark_creates_hub_entry():
    """Adding a bookmark to a new hub creates the hub entry."""
    data = {"hubs": {}}
    add_bookmark(data, "rmap", "#mesh")
    assert "rmap" in data["hubs"]
    assert data["hubs"]["rmap"]["channels"] == [{"name": "#mesh"}]


def test_add_bookmark_local_sets_null_destination():
    """Adding to 'local' pseudo-hub sets destination to null."""
    data = {"hubs": {}}
    add_bookmark(data, "local", "#test")
    assert data["hubs"]["local"]["destination"] is None


def test_add_bookmark_with_key():
    """Adding a bookmark with a key stores the key."""
    data = {"hubs": {"rmap": {"destination": "aa" * 16, "channels": []}}}
    add_bookmark(data, "rmap", "#private", "bb" * 32)
    assert data["hubs"]["rmap"]["channels"] == [{"name": "#private", "key": "bb" * 32}]


def test_add_bookmark_duplicate_is_noop():
    """Adding the same bookmark twice doesn't create duplicates."""
    data = {"hubs": {"rmap": {"destination": "aa" * 16, "channels": [{"name": "#mesh"}]}}}
    add_bookmark(data, "rmap", "#mesh")
    assert len(data["hubs"]["rmap"]["channels"]) == 1


def test_remove_bookmark():
    """Removing a bookmark removes it from the channels list."""
    data = {"hubs": {"rmap": {"destination": "aa" * 16, "channels": [{"name": "#mesh"}, {"name": "#dev"}]}}}
    remove_bookmark(data, "rmap", "#mesh")
    assert data["hubs"]["rmap"]["channels"] == [{"name": "#dev"}]


def test_remove_bookmark_nonexistent_is_noop():
    """Removing a nonexistent bookmark is a no-op."""
    data = {"hubs": {"rmap": {"destination": "aa" * 16, "channels": [{"name": "#mesh"}]}}}
    remove_bookmark(data, "rmap", "#nope")
    assert data["hubs"]["rmap"]["channels"] == [{"name": "#mesh"}]


def test_remove_bookmark_unknown_hub_is_noop():
    """Removing from an unknown hub is a no-op."""
    data = {"hubs": {}}
    remove_bookmark(data, "unknown", "#mesh")
    assert data == {"hubs": {}}


def test_corrupt_json_returns_empty(tmp_path):
    """Corrupt JSON file returns empty default."""
    path = bookmarks_path(str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{{{bad json")
    data = load_hubs(str(tmp_path))
    assert data == {"hubs": {}}


def test_wrong_structure_returns_empty(tmp_path):
    """JSON file without 'hubs' key returns empty default."""
    path = bookmarks_path(str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump([1, 2, 3], f)
    data = load_hubs(str(tmp_path))
    assert data == {"hubs": {}}
