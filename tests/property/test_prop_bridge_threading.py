"""
Property tests for thread-safe stdout writes in lxcf_bridge.

Properties validated:
  9. Thread-safe stdout writes

Feature: portulus-python-backend
Validates Requirements: 11.1
"""

from __future__ import annotations

import io
import json
import sys
import threading

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.conftest import lxcf_nick


@st.composite
def event_payloads(draw):
    """Generate a list of 2-20 event dicts to fire concurrently."""
    n = draw(st.integers(min_value=2, max_value=20))
    events = []
    for i in range(n):
        nick = draw(lxcf_nick())
        body = draw(st.text(min_size=1, max_size=200))
        events.append({
            "event": "message",
            "cid": "#test",
            "nick": nick,
            "body": body,
            "timestamp": 1710000000.0 + i,
            "suffix": f"{i:08x}",
        })
    return events


@given(payloads=event_payloads())
@settings(max_examples=200)
def test_thread_safe_stdout_writes(payloads: list[dict]):
    """
    Feature: portulus-python-backend, Property 9: Thread-safe stdout writes

    For N threads concurrently calling write_event, every line written
    to stdout is a complete, valid JSON object with no interleaving.
    """
    from lxcf_bridge import Bridge

    bridge = Bridge()

    # Redirect stdout to a StringIO for capture
    captured = io.StringIO()
    lock = threading.Lock()

    original_write = sys.stdout.write
    original_flush = sys.stdout.flush

    def safe_write(s):
        captured.write(s)

    def safe_flush():
        pass

    sys.stdout.write = safe_write
    sys.stdout.flush = safe_flush

    try:
        barrier = threading.Barrier(len(payloads))
        errors = []

        def fire(payload):
            try:
                barrier.wait(timeout=5)
                bridge.write_event(payload)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=fire, args=(p,)) for p in payloads]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
    finally:
        sys.stdout.write = original_write
        sys.stdout.flush = original_flush

    assert not errors, f"Thread errors: {errors}"

    output = captured.getvalue()
    lines = [l for l in output.split("\n") if l.strip()]

    # Every line must be valid JSON
    assert len(lines) == len(payloads), (
        f"Expected {len(payloads)} lines, got {len(lines)}"
    )

    parsed_events = []
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
            parsed_events.append(obj)
        except json.JSONDecodeError:
            assert False, f"Line {i} is not valid JSON: {line!r}"

    # Every original payload must appear exactly once
    # (order may vary due to thread scheduling)
    for payload in payloads:
        assert payload in parsed_events, (
            f"Payload not found in output: {payload}"
        )
