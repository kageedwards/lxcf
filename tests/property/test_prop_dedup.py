"""
Property tests for MessageDeduplicator.

Properties validated:
  6. Dedup Idempotency
  7. Hash Determinism

Validates Requirements: 4.1, 4.2, 4.4
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from lxcf.util import MessageDeduplicator


# --- Property 6: Dedup Idempotency ---

@given(msg_id=st.text(min_size=1, max_size=64))
@settings(max_examples=200)
def test_dedup_first_false_second_true(msg_id: str):
    """
    First call to is_duplicate returns False, second returns True.

    **Validates: Requirements 4.1, 4.2**
    """
    dedup = MessageDeduplicator()
    assert dedup.is_duplicate(msg_id) is False
    assert dedup.is_duplicate(msg_id) is True


# --- Property 7: Hash Determinism ---

@given(d=st.dictionaries(st.text(min_size=1, max_size=20), st.text(max_size=50), max_size=10))
@settings(max_examples=200)
def test_hash_message_deterministic(d: dict):
    """
    Calling hash_message twice with the same dict produces the same digest.

    **Validates: Requirements 4.4**
    """
    assert MessageDeduplicator.hash_message(d) == MessageDeduplicator.hash_message(d)
