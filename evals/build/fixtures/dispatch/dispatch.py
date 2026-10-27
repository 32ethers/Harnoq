"""Assigning queued work items to available handlers.

The queue holds items waiting for someone to work on them. ``dispatch`` picks
the next item and hands it to a free handler.
"""
from dataclasses import dataclass


class DispatchError(Exception):
    """Base class for dispatch failures."""


class NoHandlerAvailable(DispatchError):
    """Every handler is busy or offline."""


class AlreadyDispatched(DispatchError):
    """The item has already been handed to a handler."""


@dataclass
class Item:
    """One queued work item.

    Attributes:
        item_id: unique identifier.
        priority: higher numbers are worked first.
        arrived_at: monotonic arrival sequence number.
    """

    item_id: str
    priority: int
    arrived_at: int


@dataclass
class DispatchResult:
    """The outcome of one dispatch attempt.

    Attributes:
        state: "assigned" or "deferred".
        item_id: the item the attempt concerned, if any.
        handler: the handler that took it, if any.
    """

    state: str
    item_id: str | None = None
    handler: str | None = None


class Queue:
    """A priority queue of work items with a set of handlers."""

    def __init__(self, handlers: list[str]) -> None:
        """Initialise the queue.

        Args:
            handlers: names of the handlers that may take items.
        """
        self._items: list[Item] = []
        self._handlers: list[str] = list(handlers)
        self._busy: dict[str, str] = {}
        self._dispatched: set[str] = set()

    def add(self, item: Item) -> None:
        """Put an item on the queue.

        Args:
            item: the item to queue.
        """
        self._items.append(item)

    def pending(self) -> list[str]:
        """Return the queued item ids, in the order they would be worked.

        Returns:
            list[str]: item ids, highest priority first.
        """
        ordered = sorted(self._items, key=lambda i: (-i.priority, i.arrived_at))
        return [item.item_id for item in ordered]

    def dispatch(self) -> DispatchResult:
        """Hand the next item to a free handler.

        Returns:
            DispatchResult: the assignment that was made.

        Raises:
            NoHandlerAvailable: if no handler is free.
            AlreadyDispatched: if the chosen item was already handed out.
        """
        free = [h for h in self._handlers if h not in self._busy]
        if not free:
            raise NoHandlerAvailable("all handlers are busy")

        ordered = sorted(self._items, key=lambda i: (-i.priority, i.arrived_at))
        if not ordered:
            return DispatchResult(state="deferred")

        item = ordered[0]
        if item.item_id in self._dispatched:
            raise AlreadyDispatched(f"item {item.item_id} was already dispatched")

        handler = free[0]
        self._busy[handler] = item.item_id
        self._dispatched.add(item.item_id)
        self._items.remove(item)
        return DispatchResult(state="assigned", item_id=item.item_id, handler=handler)

    def complete(self, handler: str) -> None:
        """Mark a handler's current item finished, freeing the handler.

        Args:
            handler: the handler that finished.
        """
        self._busy.pop(handler, None)
