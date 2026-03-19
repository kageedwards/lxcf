"""
Property tests for the lxcf_bridge NDJSON protocol.

Properties validated:
  1. NDJSON round-trip
  2. Single-line format invariant
  3. Correlation ID round-trip
  5. Event mapping completeness
  8. Error response structure

Feature: portulus-python-backend
Validates Requirements: 2.1, 2.4, 2.5, 3.2, 4.1–4.7, 6.1, 10.1–10.4
"""

from __future__ import annotations

import io
import json
import sys
import threading
from unittest.mock import MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.conftest import lxcf_nick, lxcf_channel


# ---------------------------------------------------------------------------
# Strategies for NDJSON messages
# ---------------------------------------------------------------------------

_json_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=100),
)

_json_values = st.recursive(
    _json_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(min_size=1, max_size=20), children, max_size=5),
    ),
    max_leaves=20,
)


@st.composite
def ndjson_command(draw):
    """Generate a valid Command dict with action, id, and extra fields."""
    action = draw(st.sampled_from([
        "join", "leave", "send", "emote", "set_topic",
        "change_nick", "privmsg", "quit",
    ]))
    req_id = draw(st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1, max_size=10,
    ))
    extra = draw(st.dictionaries(
        st.text(min_size=1, max_size=10).filter(lambda k: k not in ("action", "id")),
        _json_primitives,
        max_size=3,
    ))
    return {"action": action, "id": req_id, **extra}


@st.composite
def ndjson_event(draw):
    """Generate a valid Event dict with event type and fields."""
    event_type = draw(st.sampled_from([
        "ready", "message", "join", "leave", "nick",
        "emote", "topic", "privmsg", "members",
    ]))
    extra = draw(st.dictionaries(
        st.text(min_size=1, max_size=10).filter(lambda k: k != "event"),
        _json_primitives,
        max_size=5,
    ))
    return {"event": event_type, **extra}


@st.composite
def ndjson_response(draw):
    """Generate a valid Response dict with correlation ID and fields."""
    req_id = draw(st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1, max_size=10,
    ))
    ok = draw(st.booleans())
    extra = draw(st.dictionaries(
        st.text(min_size=1, max_size=10).filter(lambda k: k not in ("response", "ok")),
        _json_primitives,
        max_size=3,
    ))
    return {"response": req_id, "ok": ok, **extra}


@st.composite
def any_ndjson_message(draw):
    """Generate any valid NDJSON message (command, event, or response)."""
    return draw(st.one_of(ndjson_command(), ndjson_event(), ndjson_response()))


# ---------------------------------------------------------------------------
# Property 1: NDJSON round-trip
# ---------------------------------------------------------------------------

@given(msg=any_ndjson_message())
@settings(max_examples=200)
def test_ndjson_round_trip(msg: dict):
    """
    Feature: portulus-python-backend, Property 1: NDJSON round-trip

    For any valid NDJSON message, json.loads(json.dumps(obj)) == obj.
    """
    serialized = json.dumps(msg, separators=(",", ":"))
    deserialized = json.loads(serialized)
    assert deserialized == msg


# ---------------------------------------------------------------------------
# Property 2: Single-line format invariant
# ---------------------------------------------------------------------------

@given(msg=any_ndjson_message())
@settings(max_examples=200)
def test_ndjson_single_line(msg: dict):
    """
    Feature: portulus-python-backend, Property 2: Single-line format invariant

    Serialized NDJSON contains no embedded newline characters.
    """
    serialized = json.dumps(msg, separators=(",", ":"))
    assert "\n" not in serialized
    assert "\r" not in serialized


# ---------------------------------------------------------------------------
# Property 3: Correlation ID round-trip
# ---------------------------------------------------------------------------

@given(cmd=ndjson_command())
@settings(max_examples=200)
def test_correlation_id_round_trip(cmd: dict):
    """
    Feature: portulus-python-backend, Property 3: Correlation ID round-trip

    For any Command with an "id" field, the Bridge's response "response"
    field equals the Command's "id" field.
    """
    from lxcf_bridge import Bridge

    bridge = Bridge()
    # Set up a local-mode client so handlers work
    from lxcf.client import Client
    bridge.client = Client(nick="test")

    # Capture stdout
    captured = io.StringIO()
    bridge._lock = threading.Lock()

    original_write = sys.stdout.write
    original_flush = sys.stdout.flush

    lines = []

    def capture_write(s):
        captured.write(s)

    def capture_flush():
        pass

    sys.stdout.write = capture_write
    sys.stdout.flush = capture_flush
    try:
        # For most actions we need a joined channel
        if cmd["action"] in ("leave", "send", "emote", "set_topic"):
            ch = bridge.client.join("#test", announce=False)
            if cmd["action"] == "leave":
                cmd["cid"] = ch._cid
            elif cmd["action"] in ("send", "emote"):
                cmd["cid"] = ch._cid
                cmd["body"] = "test"
            elif cmd["action"] == "set_topic":
                cmd["cid"] = ch._cid
                cmd["topic"] = "test topic"
        elif cmd["action"] == "join":
            cmd["channel"] = "#test"
        elif cmd["action"] == "change_nick":
            cmd["nick"] = "newnick"
        elif cmd["action"] == "privmsg":
            cmd["dest_hash"] = "aa" * 16
            cmd["body"] = "hello"
        elif cmd["action"] == "quit":
            # Skip quit — it calls sys.exit
            return

        bridge._dispatch(cmd)
    finally:
        sys.stdout.write = original_write
        sys.stdout.flush = original_flush

    output = captured.getvalue()
    # Find the response line (has "response" key)
    for line in output.strip().split("\n"):
        if not line:
            continue
        parsed = json.loads(line)
        if "response" in parsed:
            assert parsed["response"] == cmd["id"]
            return

    # If we got here, no response was found — that's a failure
    assert False, f"No response found for command {cmd}"


# ---------------------------------------------------------------------------
# Property 5: Event mapping completeness
# ---------------------------------------------------------------------------

# Required fields per event type (from design doc)
EVENT_REQUIRED_FIELDS = {
    "message": {"event", "cid", "nick", "body", "timestamp", "suffix"},
    "join": {"event", "cid", "nick", "suffix"},
    "leave": {"event", "cid", "nick"},
    "nick": {"event", "old_nick", "new_nick"},
    "emote": {"event", "cid", "nick", "body", "timestamp", "suffix"},
    "topic": {"event", "cid", "nick", "body"},
    "privmsg": {"event", "nick", "body", "timestamp", "suffix"},
}


@given(
    event_type=st.sampled_from(sorted(EVENT_REQUIRED_FIELDS.keys())),
    nick=lxcf_nick(),
    body=st.text(min_size=1, max_size=100),
)
@settings(max_examples=200)
def test_event_mapping_completeness(event_type: str, nick: str, body: str):
    """
    Feature: portulus-python-backend, Property 5: Event mapping completeness

    For each LXCF event type, the NDJSON event written to stdout contains
    the correct "event" discriminator and all required fields.
    """
    from lxcf_bridge import Bridge
    from lxcf.client import Client

    bridge = Bridge()
    bridge.client = Client(nick=nick)
    ch = bridge.client.join("#test", announce=False)
    bridge._wire_events()

    captured = io.StringIO()
    sys.stdout.write, sys.stdout.flush = captured.write, lambda: None
    try:
        if event_type == "message":
            from lxcf.message import LXCFMessage
            msg = LXCFMessage.chat("other", "#test", body)
            msg.timestamp = 1710000000.0
            ch._record(msg, source_hash=b"\xaa" * 16)
            bridge.client.events.emit("message", ch, msg)
        elif event_type == "join":
            ch._member_join("joiner", source_hash=b"\xbb" * 16)
            bridge.client.events.emit("join", ch, "joiner")
        elif event_type == "leave":
            bridge.client.events.emit("leave", ch, "leaver")
        elif event_type == "nick":
            bridge.client.events.emit("nick", "oldname", "newname")
        elif event_type == "emote":
            from lxcf.message import LXCFMessage
            msg = LXCFMessage.emote("other", "#test", body)
            msg.timestamp = 1710000000.0
            ch._record(msg, source_hash=b"\xcc" * 16)
            bridge.client.events.emit("emote", ch, msg)
        elif event_type == "topic":
            from lxcf.message import LXCFMessage
            msg = LXCFMessage.topic("other", "#test", body)
            bridge.client.events.emit("topic", ch, msg)
        elif event_type == "privmsg":
            from lxcf.message import LXCFMessage
            msg = LXCFMessage.privmsg("sender", body)
            msg.timestamp = 1710000000.0
            bridge.client.events.emit("privmsg", b"\xdd" * 16, msg)
    finally:
        sys.stdout.write = sys.__stdout__.write
        sys.stdout.flush = sys.__stdout__.flush

    output = captured.getvalue()
    # Find the first event line matching our event_type
    for line in output.strip().split("\n"):
        if not line:
            continue
        parsed = json.loads(line)
        if parsed.get("event") == event_type:
            required = EVENT_REQUIRED_FIELDS[event_type]
            missing = required - set(parsed.keys())
            assert not missing, f"Missing fields {missing} in {event_type} event: {parsed}"
            return

    assert False, f"No {event_type} event found in output: {output!r}"


# ---------------------------------------------------------------------------
# Property 8: Error response structure
# ---------------------------------------------------------------------------

@given(
    req_id=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1, max_size=10,
    ),
    action=st.sampled_from(["send", "emote", "set_topic"]),
)
@settings(max_examples=200)
def test_error_response_structure(req_id: str, action: str):
    """
    Feature: portulus-python-backend, Property 8: Error response structure

    For any Command that causes an exception, the Response contains
    "ok": false, a non-empty "error" string, and "response" matching
    the Correlation ID.
    """
    from lxcf_bridge import Bridge
    from lxcf.client import Client

    bridge = Bridge()
    bridge.client = Client(nick="test")
    # Don't join any channel — commands that need a channel will fail

    captured = io.StringIO()
    sys.stdout.write, sys.stdout.flush = captured.write, lambda: None
    try:
        cmd = {"action": action, "id": req_id, "cid": "#nonexistent", "body": "x", "topic": "x"}
        bridge._dispatch(cmd)
    finally:
        sys.stdout.write = sys.__stdout__.write
        sys.stdout.flush = sys.__stdout__.flush

    output = captured.getvalue()
    for line in output.strip().split("\n"):
        if not line:
            continue
        parsed = json.loads(line)
        if "response" in parsed:
            assert parsed["response"] == req_id
            assert parsed["ok"] is False
            assert isinstance(parsed["error"], str)
            assert len(parsed["error"]) > 0
            return

    assert False, f"No error response found in output: {output!r}"
