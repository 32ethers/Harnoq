"""Tests for queue dispatch."""
import pytest

from dispatch import Item, NoHandlerAvailable, Queue


def _queue(handlers=("h1",)):
    return Queue(list(handlers))


def test_higher_priority_is_worked_first():
    q = _queue()
    q.add(Item("low", priority=1, arrived_at=1))
    q.add(Item("high", priority=9, arrived_at=2))
    assert q.pending() == ["high", "low"]


def test_same_priority_keeps_arrival_order():
    q = _queue()
    q.add(Item("second", priority=5, arrived_at=2))
    q.add(Item("first", priority=5, arrived_at=1))
    assert q.pending() == ["first", "second"]


def test_dispatch_assigns_the_top_item():
    q = _queue()
    q.add(Item("a", priority=5, arrived_at=1))
    result = q.dispatch()
    assert result.state == "assigned"
    assert result.item_id == "a"
    assert result.handler == "h1"


def test_a_dispatched_item_leaves_the_queue():
    q = _queue()
    q.add(Item("a", priority=5, arrived_at=1))
    q.dispatch()
    assert q.pending() == []


def test_busy_handlers_block_further_dispatch():
    q = _queue()
    q.add(Item("a", priority=5, arrived_at=1))
    q.add(Item("b", priority=4, arrived_at=2))
    q.dispatch()
    with pytest.raises(NoHandlerAvailable):
        q.dispatch()


def test_completing_frees_the_handler():
    q = _queue()
    q.add(Item("a", priority=5, arrived_at=1))
    q.add(Item("b", priority=4, arrived_at=2))
    q.dispatch()
    q.complete("h1")
    result = q.dispatch()
    assert result.state == "assigned"
    assert result.item_id == "b"


def test_an_empty_queue_defers():
    q = _queue()
    assert q.dispatch().state == "deferred"


def test_two_handlers_take_two_items():
    q = _queue(("h1", "h2"))
    q.add(Item("a", priority=5, arrived_at=1))
    q.add(Item("b", priority=4, arrived_at=2))
    first = q.dispatch()
    second = q.dispatch()
    assert {first.item_id, second.item_id} == {"a", "b"}
    assert first.handler != second.handler
