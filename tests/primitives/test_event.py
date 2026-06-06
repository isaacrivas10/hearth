"""Tests for the Event primitive — see docs/core/primitives/event.md."""

import pytest
from pydantic import ValidationError

from hearth import EntityId, Event


class _OrderPlaced(Event):
    order_id: EntityId
    total_cents: int


def test_event_carries_payload() -> None:
    oid = EntityId.new()
    e = _OrderPlaced(order_id=oid, total_cents=100)
    assert e.order_id == oid
    assert e.total_cents == 100


def test_event_is_frozen() -> None:
    e = _OrderPlaced(order_id=EntityId.new(), total_cents=100)
    with pytest.raises(ValidationError):
        e.total_cents = 200  # type: ignore[misc]


def test_event_structural_equality() -> None:
    oid = EntityId.new()
    a = _OrderPlaced(order_id=oid, total_cents=100)
    b = _OrderPlaced(order_id=oid, total_cents=100)
    c = _OrderPlaced(order_id=oid, total_cents=200)
    assert a == b
    assert a != c
    assert hash(a) == hash(b)


def test_event_json_roundtrip() -> None:
    e = _OrderPlaced(order_id=EntityId.new(), total_cents=100)
    assert _OrderPlaced.model_validate_json(e.model_dump_json()) == e


def test_event_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        _OrderPlaced.model_validate(
            {
                "order_id": str(EntityId.new()),
                "total_cents": 100,
                "extra": "nope",
            }
        )
