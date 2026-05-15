"""Tests for the in-memory (SQLite-backed) UnitOfWork implementation."""

from collections.abc import AsyncIterator

import pytest

from hearth import Entity, EntityId, EntityNotFoundError, Event
from hearth.testing import Harness


class _UowCust(Entity, plugin="test_uow"):
    name: str


class _CreatedEvent(Event):
    customer_id: EntityId
    name: str


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    h = Harness()
    await h.setup(entities=[_UowCust])
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def test_save_persists_with_auto_assigned_id(harness: Harness) -> None:
    c = _UowCust(name="Alice")
    # Auto-id is assigned at construction so plugin code can use it
    # before save completes (e.g., for emit referencing customer.id).
    assert c.id is not None
    async with harness.transaction() as uow:
        await uow.save(c)
    assert c.id is not None


async def test_get_retrieves_committed_entity(harness: Harness) -> None:
    c = _UowCust(name="Alice")
    async with harness.transaction() as uow:
        await uow.save(c)

    assert c.id is not None
    async with harness.transaction() as uow:
        loaded = await uow.get(_UowCust, c.id)

    assert loaded == c
    assert loaded.name == "Alice"


async def test_get_raises_on_missing(harness: Harness) -> None:
    bogus = EntityId.new()
    async with harness.transaction() as uow:
        with pytest.raises(EntityNotFoundError):
            await uow.get(_UowCust, bogus)


async def test_within_transaction_save_visible_to_get(harness: Harness) -> None:
    c = _UowCust(name="Alice")
    async with harness.transaction() as uow:
        await uow.save(c)
        assert c.id is not None
        loaded = await uow.get(_UowCust, c.id)
        assert loaded.name == "Alice"


async def test_emit_visible_after_commit(harness: Harness) -> None:
    cid = EntityId.new()
    async with harness.transaction() as uow:
        uow.emit(_CreatedEvent(customer_id=cid, name="Alice"))

    events = await harness.events_of_type(_CreatedEvent)
    assert len(events) == 1
    assert events[0].name == "Alice"


async def test_rollback_discards_save(harness: Harness) -> None:
    c = _UowCust(name="Alice")
    with pytest.raises(RuntimeError):
        async with harness.transaction() as uow:
            await uow.save(c)
            raise RuntimeError("simulated failure")

    assert c.id is not None  # id was assigned at save
    async with harness.transaction() as uow:
        with pytest.raises(EntityNotFoundError):
            await uow.get(_UowCust, c.id)


async def test_rollback_discards_emitted_events(harness: Harness) -> None:
    cid = EntityId.new()
    with pytest.raises(RuntimeError):
        async with harness.transaction() as uow:
            uow.emit(_CreatedEvent(customer_id=cid, name="Alice"))
            raise RuntimeError("simulated failure")

    assert len(await harness.events_of_type(_CreatedEvent)) == 0


async def test_save_and_emit_atomically(harness: Harness) -> None:
    c = _UowCust(name="Alice")
    async with harness.transaction() as uow:
        await uow.save(c)
        assert c.id is not None
        uow.emit(_CreatedEvent(customer_id=c.id, name=c.name))

    async with harness.transaction() as uow:
        assert await uow.get(_UowCust, c.id) == c
    assert len(await harness.events_of_type(_CreatedEvent)) == 1


async def test_delete_removes_committed_entity(harness: Harness) -> None:
    c = _UowCust(name="Alice")
    async with harness.transaction() as uow:
        await uow.save(c)

    assert c.id is not None
    async with harness.transaction() as uow:
        await uow.delete(c)

    async with harness.transaction() as uow:
        with pytest.raises(EntityNotFoundError):
            await uow.get(_UowCust, c.id)


async def test_query_where_filters_results(harness: Harness) -> None:
    async with harness.transaction() as uow:
        await uow.save(_UowCust(name="Alice"))
        await uow.save(_UowCust(name="Bob"))

    async with harness.transaction() as uow:
        found = await uow.query(_UowCust).where(_UowCust.name == "Alice").all()

    assert [c.name for c in found] == ["Alice"]


async def test_query_count(harness: Harness) -> None:
    async with harness.transaction() as uow:
        await uow.save(_UowCust(name="Alice"))
        await uow.save(_UowCust(name="Bob"))
        await uow.save(_UowCust(name="Carol"))

    async with harness.transaction() as uow:
        total = await uow.query(_UowCust).count()

    assert total == 3


async def test_query_order_by_and_limit(harness: Harness) -> None:
    async with harness.transaction() as uow:
        for name in ("Carol", "Alice", "Bob"):
            await uow.save(_UowCust(name=name))

    async with harness.transaction() as uow:
        top_two = await uow.query(_UowCust).order_by(_UowCust.name).limit(2).all()

    assert [c.name for c in top_two] == ["Alice", "Bob"]
