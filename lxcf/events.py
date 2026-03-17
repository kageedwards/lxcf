"""
LXCF EventBus — lightweight pub/sub for client-side event handling.
"""

from collections import defaultdict
from typing import Callable


class EventBus:
    """
    Simple event emitter.

    Usage::

        bus = EventBus()

        @bus.on("message")
        def handle(msg):
            print(msg)

        bus.emit("message", some_msg)
    """

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def on(self, event: str, fn: Callable | None = None):
        """Register a handler.  Works as a decorator or direct call."""
        if fn is not None:
            self._handlers[event].append(fn)
            return fn

        def decorator(func: Callable):
            self._handlers[event].append(func)
            return func
        return decorator

    def off(self, event: str, fn: Callable):
        """Remove a specific handler."""
        try:
            self._handlers[event].remove(fn)
        except ValueError:
            pass

    def emit(self, event: str, *args, **kwargs):
        """Fire all handlers registered for *event*."""
        for handler in self._handlers.get(event, []):
            handler(*args, **kwargs)
