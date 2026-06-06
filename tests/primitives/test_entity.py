"""Tests for the Entity primitive — see docs/core/primitives/entity.md."""

import pytest
from pydantic import ValidationError

from hearth import Entity, EntityId


class _Customer(Entity, plugin="commerce"):
    name: str
    age: int = 0


def test_entity_subclass_records_plugin() -> None:
    assert _Customer._hearth_plugin == "commerce"


def test_entity_id_is_assigned_at_construction() -> None:
    c = _Customer(name="Alice")
    assert c.id is not None
    assert isinstance(c.id, EntityId)
    assert len(c.id) == 26  # ULID Crockford-base32 form


def test_entity_field_access() -> None:
    c = _Customer(name="Alice", age=30)
    assert c.name == "Alice"
    assert c.age == 30


def test_entity_is_mutable() -> None:
    c = _Customer(name="Alice")
    c.name = "Bob"
    assert c.name == "Bob"


def test_entity_validation_runs_on_init() -> None:
    with pytest.raises(ValidationError):
        _Customer(name=123)  # type: ignore[arg-type]


def test_entity_validation_runs_on_assignment() -> None:
    c = _Customer(name="Alice")
    with pytest.raises(ValidationError):
        c.name = 123  # type: ignore[assignment]


def test_distinct_entities_have_distinct_ids() -> None:
    a = _Customer(name="Alice")
    b = _Customer(name="Alice")
    assert a.id != b.id  # auto-generated IDs differ
    assert a != b
    assert a == a


def test_entities_with_same_id_are_equal() -> None:
    a = _Customer(name="Alice")
    b = _Customer(name="Different")
    same_id = EntityId.new()
    a.id = same_id
    b.id = same_id
    assert a == b


def test_entities_of_different_types_are_not_equal() -> None:
    class _Other(Entity, plugin="commerce"):
        name: str

    a = _Customer(name="Alice")
    b = _Other(name="Alice")
    same_id = EntityId.new()
    a.id = same_id
    b.id = same_id
    assert a != b


def test_entity_is_hashable_via_id() -> None:
    c = _Customer(name="Alice")
    assert hash(c) == hash(c)  # stable
    assert c in {c}  # round-trips through a set


def test_saved_entities_with_same_id_hash_the_same() -> None:
    a = _Customer(name="Alice")
    b = _Customer(name="Different")
    same_id = EntityId.new()
    a.id = same_id
    b.id = same_id
    assert hash(a) == hash(b)


def test_validate_method_for_cross_field_invariants() -> None:
    class _Order(Entity, plugin="test_entity_validate"):
        placed_at: int
        delivered_at: int | None = None

        def validate(self) -> None:
            if self.delivered_at is not None and self.delivered_at < self.placed_at:
                raise ValueError("cannot deliver before placement")

    _Order(placed_at=10, delivered_at=20)
    with pytest.raises(ValueError, match="cannot deliver"):
        _Order(placed_at=10, delivered_at=5)


def test_unknown_kwargs_rejected_at_construction() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        _Customer(name="Alice", extra="nope")  # type: ignore[call-arg]
