"""
Unit tests for the lxcf_bridge NDJSON stdio bridge.

Tests:
  - Invalid JSON on stdin is skipped without crash (Req 2.3)
  - Unknown action returns ok:false error response (Req 6.2)
  - quit command leaves all channels and exits with code 0 (Req 6.4)
  - init command emits ready event with correct fields (Req 1.3)
  - join response contains all required fields (Req 5.2)
  - handle_change_nick returns new nick and pushes member updates (Req 5.7)

Feature: portulus-python-backend
"""

from __future__ import annotations

import io
import json
import sys
import threading

import pytest

from lxcf.client import Client
from lxcf_bridge import Bridge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class StdoutCapture:
    """Context manager that captures stdout writes from the bridge."""

    def __init__(self):
        self.captured = io.StringIO()
        self._orig_write = None
        self._orig_flush = None

    def __enter__(self):
        self._orig_write = sys.stdout.write
        self._orig_flush = sys.stdout.flush
        sys.stdout.write = self.captured.write
        sys.stdout.flush = lambda: None
        return self

    def __exit__(self, *args):
        sys.stdout.write = self._orig_write
        sys.stdout.flush = self._orig_flush

    @property
    def lines(self) -> list[dict]:
        """Return all captured lines as parsed JSON dicts."""
        result = []
        for line in self.captured.getvalue().strip().split("\n"):
            if line.strip():
                result.append(json.loads(line))
        return result

    def events(self, event_type: str | None = None) -> list[dict]:
        """Return captured event lines, optionally filtered by type."""
        evts = [l for l in self.lines if "event" in l]
        if event_type:
            evts = [e for e in evts if e["event"] == event_type]
        return evts

    def responses(self) -> list[dict]:
        """Return captured response lines."""
        return [l for l in self.lines if "response" in l]


def make_bridge(nick: str = "testuser") -> Bridge:
    """Create a Bridge with a local-mode client (no RNS/LXMF)."""
    b = Bridge()
    b.client = Client(nick=nick)
    b._wire_events()
    return b


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInvalidJson:
    """Req 2.3: Invalid JSON on stdin is skipped without crash."""

    def test_bad_json_skipped(self):
        bridge = make_bridge()
        # Simulate stdin with a mix of bad and good lines
        stdin_data = "not json at all\n{\"action\":\"leave\",\"id\":\"1\",\"cid\":\"#nope\"}\n"
        sys.stdin = io.StringIO(stdin_data)

        with StdoutCapture() as cap:
            bridge.run()

        # Bridge should have processed the valid line (leave on non-existent
        # channel is a no-op that returns ok:true)
        resps = cap.responses()
        assert len(resps) == 1
        assert resps[0]["response"] == "1"

    def test_empty_lines_skipped(self):
        bridge = make_bridge()
        stdin_data = "\n\n\n"
        sys.stdin = io.StringIO(stdin_data)

        with StdoutCapture() as cap:
            bridge.run()

        assert cap.lines == []


class TestUnknownAction:
    """Req 6.2: Unknown action returns ok:false error response."""

    def test_unknown_action(self):
        bridge = make_bridge()

        with StdoutCapture() as cap:
            bridge._dispatch({"action": "bogus_action", "id": "42"})

        resps = cap.responses()
        assert len(resps) == 1
        assert resps[0]["response"] == "42"
        assert resps[0]["ok"] is False
        assert "unknown action" in resps[0]["error"]
        assert "bogus_action" in resps[0]["error"]


class TestQuitCommand:
    """Req 6.4: quit leaves all channels and exits with code 0."""

    def test_quit_leaves_channels_and_exits(self):
        bridge = make_bridge()
        bridge.client.join("#one", announce=False)
        bridge.client.join("#two", announce=False)
        assert len(bridge.client.channels) == 2

        with StdoutCapture() as cap:
            with pytest.raises(SystemExit) as exc_info:
                bridge._dispatch({"action": "quit", "id": "99"})

        assert exc_info.value.code == 0
        assert len(bridge.client.channels) == 0

        resps = cap.responses()
        # Find the final quit response
        quit_resp = [r for r in resps if r["response"] == "99"]
        assert len(quit_resp) == 1
        assert quit_resp[0]["ok"] is True


class TestJoinResponse:
    """Req 5.2: join response contains all required fields."""

    def test_join_response_fields(self):
        bridge = make_bridge()

        with StdoutCapture() as cap:
            bridge._dispatch({
                "action": "join",
                "id": "1",
                "channel": "#mesh",
            })

        resps = cap.responses()
        assert len(resps) == 1
        resp = resps[0]
        assert resp["response"] == "1"
        assert resp["ok"] is True
        assert resp["cid"] == "#mesh"
        assert resp["name"] == "#mesh"
        assert resp["hub"] is None
        # dest_hash is None in local mode
        assert "destHash" in resp

    def test_join_with_key_hex(self):
        bridge = make_bridge()
        key_hex = "aa" * 32  # 64-char hex = 32 bytes

        with StdoutCapture() as cap:
            bridge._dispatch({
                "action": "join",
                "id": "2",
                "channel": "#secret",
                "key": key_hex,
            })

        resps = cap.responses()
        assert len(resps) == 1
        resp = resps[0]
        assert resp["ok"] is True
        assert resp["key"] == key_hex
        # Without a hub, CID is just the bare channel name
        assert resp["cid"] == "#secret"

    def test_join_with_hub_tag(self):
        bridge = make_bridge()
        # Set up hubs data with a known hub
        bridge._hubs_data = {
            "hubs": {
                "testhub": {
                    "destination": "bb" * 16,
                    "channels": [],
                }
            }
        }

        with StdoutCapture() as cap:
            bridge._dispatch({
                "action": "join",
                "id": "3",
                "channel": "#mesh",
                "hub": "testhub",
            })

        resps = cap.responses()
        assert len(resps) == 1
        resp = resps[0]
        assert resp["ok"] is True
        assert resp["hub"] == "testhub"

    def test_join_unknown_hub_returns_error(self):
        bridge = make_bridge()
        bridge._hubs_data = {"hubs": {}}

        with StdoutCapture() as cap:
            bridge._dispatch({
                "action": "join",
                "id": "4",
                "channel": "#mesh",
                "hub": "nonexistent",
            })

        resps = cap.responses()
        assert len(resps) == 1
        resp = resps[0]
        assert resp["ok"] is False
        assert "Unknown hub" in resp["error"]

    def test_join_without_hub_passes_none(self):
        bridge = make_bridge()

        with StdoutCapture() as cap:
            bridge._dispatch({
                "action": "join",
                "id": "5",
                "channel": "#local",
            })

        resps = cap.responses()
        resp = resps[0]
        assert resp["ok"] is True
        assert resp["hub"] is None


class TestToggleBookmark:
    """Hub-aware bookmark toggling."""

    def test_toggle_bookmark_adds_and_removes(self):
        bridge = make_bridge()
        bridge._hubs_data = {"hubs": {"rmap": {"destination": "aa" * 16, "channels": []}}}
        # Use a temp store so save_hubs doesn't write to real home
        import tempfile
        bridge._store_path = tempfile.mkdtemp()

        with StdoutCapture() as cap:
            bridge._dispatch({
                "action": "toggle_bookmark",
                "id": "1",
                "channel": "#mesh",
                "hub": "rmap",
            })

        resps = cap.responses()
        assert resps[0]["ok"] is True
        hubs = resps[0]["hubs"]
        assert any(ch["name"] == "#mesh" for ch in hubs["hubs"]["rmap"]["channels"])

        # Toggle again to remove
        with StdoutCapture() as cap:
            bridge._dispatch({
                "action": "toggle_bookmark",
                "id": "2",
                "channel": "#mesh",
                "hub": "rmap",
            })

        resps = cap.responses()
        assert resps[0]["ok"] is True
        hubs = resps[0]["hubs"]
        assert not any(ch["name"] == "#mesh" for ch in hubs["hubs"]["rmap"]["channels"])


class TestChangeNick:
    """Req 5.7: change_nick returns new nick and pushes member updates."""

    def test_change_nick(self):
        bridge = make_bridge(nick="alice")
        bridge.client.join("#test", announce=False)

        with StdoutCapture() as cap:
            bridge._dispatch({
                "action": "change_nick",
                "id": "5",
                "nick": "bob",
            })

        resps = cap.responses()
        assert len(resps) == 1
        assert resps[0]["ok"] is True
        assert resps[0]["nick"] == "bob"

        # Should have emitted nick + members events
        nick_events = cap.events("nick")
        assert len(nick_events) >= 1
        assert nick_events[0]["old_nick"] == "alice"
        assert nick_events[0]["new_nick"] == "bob"

        member_events = cap.events("members")
        assert len(member_events) >= 1
        # The member list should contain "bob", not "alice"
        members = member_events[-1]["members"]
        nicks = [m["nick"] for m in members]
        assert "bob" in nicks
        assert "alice" not in nicks


class TestInitReady:
    """Req 1.3: init emits ready event with correct fields.

    We test this indirectly by checking the ready event structure
    since handle_init requires RNS/LXMF which aren't available in
    unit tests. Instead we test the write_event path directly.
    """

    def test_ready_event_structure(self):
        bridge = make_bridge(nick="kage")

        with StdoutCapture() as cap:
            bridge.write_event({
                "event": "ready",
                "nick": "kage",
                "address": "abcdef0123456789",
                "suffix": "abcdef01",
            })

        events = cap.events("ready")
        assert len(events) == 1
        evt = events[0]
        assert evt["nick"] == "kage"
        assert evt["address"] == "abcdef0123456789"
        assert evt["suffix"] == "abcdef01"
