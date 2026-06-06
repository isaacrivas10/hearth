"""Tests for the Postgres-backed UnitOfWork.

Requires DATABASE_URL pointing at a Postgres instance Hearth can drop /
recreate tables in. Skips if the env var is unset.
"""

import os
from collections.abc import AsyncIterator

import pytest

from hearth import Entity, EntityId, EntityNotFoundError, Event, Value, field_validator
from hearth.testing import PostgresHarness

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="DATABASE_URL not set; skipping Postgres tests",
)


class _Phone(Value):
    raw: str

    @field_validator("raw")
    @classmethod
    def _e164(cls, v: str) -> str:
        if not v.startswith("+") or len(v) < 4:
            raise ValueError("must look E.164-ish")
        return v


class _Cust(Entity, plugin="pg_test"):
    name: str
    phone: _Phone
    note: str | None = None


class _CreatedEvent(Event):
    customer_id: EntityId
    name: str


@pytest.fixture
async def pg_harness() -> AsyncIterator[PostgresHarness]:
    h = PostgresHarness(os.environ["DATABASE_URL"])
    await h.setup(entities=[_Cust])
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def test_save_and_get_roundtrips_via_postgres(pg_harness: PostgresHarness) -> None:
    c = _Cust(name="Alice", phone=_Phone(raw="+50499998888"))
    async with pg_harness.transaction() as uow:
        await uow.save(c)

    assert c.id is not None
    async with pg_harness.transaction() as uow:
        loaded = await uow.get(_Cust, c.id)

    assert loaded.name == "Alice"
    assert loaded.phone == _Phone(raw="+50499998888")
    assert loaded.id == c.id


async def test_get_raises_for_missing_id(pg_harness: PostgresHarness) -> None:
    bogus = EntityId.new()
    async with pg_harness.transaction() as uow:
        with pytest.raises(EntityNotFoundError):
            await uow.get(_Cust, bogus)


async def test_query_by_value_field(pg_harness: PostgresHarness) -> None:
    phone = _Phone(raw="+50499998888")
    c = _Cust(name="Alice", phone=phone)
    async with pg_harness.transaction() as uow:
        await uow.save(c)

    async with pg_harness.transaction() as uow:
        found = await uow.query(_Cust).where(_Cust.phone == phone).one_or_none()
        missing = (
            await uow.query(_Cust).where(_Cust.phone == _Phone(raw="+11111111111")).one_or_none()
        )

    assert found is not None
    assert found.id == c.id
    assert missing is None


async def test_emit_writes_outbox_row_in_same_transaction(
    pg_harness: PostgresHarness,
) -> None:
    c = _Cust(name="Alice", phone=_Phone(raw="+50499998888"))
    async with pg_harness.transaction() as uow:
        await uow.save(c)
        assert c.id is not None
        uow.emit(_CreatedEvent(customer_id=c.id, name=c.name))

    events = await pg_harness.events_of_type(_CreatedEvent)
    assert len(events) == 1
    assert events[0].name == "Alice"
    assert events[0].customer_id == c.id


async def test_rollback_discards_save_and_event(pg_harness: PostgresHarness) -> None:
    c = _Cust(name="Alice", phone=_Phone(raw="+50499998888"))
    with pytest.raises(RuntimeError):
        async with pg_harness.transaction() as uow:
            await uow.save(c)
            assert c.id is not None
            uow.emit(_CreatedEvent(customer_id=c.id, name=c.name))
            raise RuntimeError("simulated failure inside transaction")

    assert c.id is not None
    async with pg_harness.transaction() as uow:
        with pytest.raises(EntityNotFoundError):
            await uow.get(_Cust, c.id)

    events = await pg_harness.events_of_type(_CreatedEvent)
    assert events == []


async def test_save_upserts_existing_row(pg_harness: PostgresHarness) -> None:
    c = _Cust(name="Alice", phone=_Phone(raw="+50499998888"))
    async with pg_harness.transaction() as uow:
        await uow.save(c)

    c.name = "Alice Updated"
    async with pg_harness.transaction() as uow:
        await uow.save(c)

    assert c.id is not None
    async with pg_harness.transaction() as uow:
        loaded = await uow.get(_Cust, c.id)
    assert loaded.name == "Alice Updated"


async def test_delete_removes_row(pg_harness: PostgresHarness) -> None:
    c = _Cust(name="Alice", phone=_Phone(raw="+50499998888"))
    async with pg_harness.transaction() as uow:
        await uow.save(c)

    assert c.id is not None
    async with pg_harness.transaction() as uow:
        await uow.delete(c)

    async with pg_harness.transaction() as uow:
        with pytest.raises(EntityNotFoundError):
            await uow.get(_Cust, c.id)
