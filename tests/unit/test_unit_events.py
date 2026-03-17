"""Unit tests for lxcf.events.EventBus."""

from lxcf.events import EventBus


# ------------------------------------------------------------------
# on() as decorator and direct call
# ------------------------------------------------------------------

def test_on_as_decorator():
    bus = EventBus()
    log = []

    @bus.on("ping")
    def handler(val):
        log.append(val)

    bus.emit("ping", 42)
    assert log == [42]


def test_on_as_direct_call():
    bus = EventBus()
    log = []

    def handler(val):
        log.append(val)

    bus.on("ping", handler)
    bus.emit("ping", 99)
    assert log == [99]


# ------------------------------------------------------------------
# off() for unregistered handler raises no exception
# ------------------------------------------------------------------

def test_off_unregistered_handler_no_exception():
    bus = EventBus()

    def never_registered():
        pass

    # Should not raise
    bus.off("ping", never_registered)


# ------------------------------------------------------------------
# emit() with positional and keyword arguments
# ------------------------------------------------------------------

def test_emit_with_positional_args():
    bus = EventBus()
    log = []

    bus.on("evt", lambda a, b: log.append((a, b)))
    bus.emit("evt", "x", "y")
    assert log == [("x", "y")]


def test_emit_with_keyword_args():
    bus = EventBus()
    log = []

    def handler(a, key=None):
        log.append((a, key))

    bus.on("evt", handler)
    bus.emit("evt", "x", key="val")
    assert log == [("x", "val")]


def test_emit_with_mixed_args_and_kwargs():
    bus = EventBus()
    log = []

    def handler(*args, **kwargs):
        log.append((args, kwargs))

    bus.on("evt", handler)
    bus.emit("evt", 1, 2, foo="bar")
    assert log == [((1, 2), {"foo": "bar"})]
