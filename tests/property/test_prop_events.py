"""
Property tests for EventBus.

Properties validated:
  10. EventBus Handler Ordering
  11. EventBus off() Precision
  17. EventBus Argument Forwarding

Validates Requirements: 5.1, 5.2, 5.5
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from lxcf.events import EventBus


# --- Property 10: EventBus Handler Ordering ---

@given(n=st.integers(min_value=1, max_value=20))
@settings(max_examples=200)
def test_eventbus_handler_order(n: int):
    """
    N handlers registered on the same event fire in registration order.

    **Validates: Requirements 5.1**
    """
    bus = EventBus()
    log: list[int] = []

    for i in range(n):
        # Use default-arg capture to bind the current value of i
        bus.on("test", lambda _i=i: log.append(_i))

    bus.emit("test")
    assert log == list(range(n))


# --- Property 11: EventBus off() Precision ---

@given(event=st.text(min_size=1, max_size=30))
@settings(max_examples=200)
def test_eventbus_off_precision(event: str):
    """
    Removing one handler leaves the other intact.

    **Validates: Requirements 5.2**
    """
    bus = EventBus()
    log: list[str] = []

    def handler_a():
        log.append("a")

    def handler_b():
        log.append("b")

    bus.on(event, handler_a)
    bus.on(event, handler_b)
    bus.off(event, handler_a)
    bus.emit(event)

    assert log == ["b"]


# --- Property 17: EventBus Argument Forwarding ---

@given(
    args=st.lists(st.integers(), min_size=0, max_size=5),
    kwargs=st.dictionaries(
        st.text(alphabet=st.characters(whitelist_categories=("L",)), min_size=1, max_size=10),
        st.integers(),
        max_size=5,
    ),
)
@settings(max_examples=200)
def test_eventbus_argument_forwarding(args: list, kwargs: dict):
    """
    emit passes all positional and keyword arguments to the handler.

    **Validates: Requirements 5.5**
    """
    bus = EventBus()
    received: list[tuple] = []

    def handler(*a, **kw):
        received.append((a, kw))

    bus.on("evt", handler)
    bus.emit("evt", *args, **kwargs)

    assert len(received) == 1
    assert received[0] == (tuple(args), kwargs)
