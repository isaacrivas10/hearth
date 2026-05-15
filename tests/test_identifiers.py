"""Tests for EntityId — see docs/core/identifiers.md."""

import pytest
from pydantic import BaseModel, ValidationError

from hearth import EntityId


def test_new_returns_subclass_of_str() -> None:
    eid = EntityId.new()
    assert isinstance(eid, str)
    assert isinstance(eid, EntityId)
    assert len(eid) == 26  # ULID Crockford-base32 form


def test_new_returns_unique_ids() -> None:
    ids = {EntityId.new() for _ in range(100)}
    assert len(ids) == 100


def test_pydantic_accepts_entity_id() -> None:
    class _Model(BaseModel):
        id: EntityId

    eid = EntityId.new()
    m = _Model(id=eid)
    assert m.id == eid
    assert isinstance(m.id, EntityId)


def test_pydantic_validates_string_to_entity_id() -> None:
    class _Model(BaseModel):
        id: EntityId

    raw = str(EntityId.new())
    m = _Model.model_validate({"id": raw})
    assert m.id == raw
    assert isinstance(m.id, EntityId)


def test_pydantic_rejects_non_ulid_string() -> None:
    class _Model(BaseModel):
        id: EntityId

    with pytest.raises(ValidationError):
        _Model.model_validate({"id": "not-a-ulid"})


def test_pydantic_rejects_wrong_length_string() -> None:
    class _Model(BaseModel):
        id: EntityId

    with pytest.raises(ValidationError):
        _Model.model_validate({"id": "01J6KZQVZNB97V21G9MM4XB"})  # too short
