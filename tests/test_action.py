"""Tests for the Action primitive."""

from collections.abc import AsyncIterator

import pytest
from pydantic import ValidationError

from hearth import Entity, EntityId, Event
from hearth.primitives.action import Action
from hearth.primitives.identity import System, User
from hearth.testing import Harness


class _ActionCust(Entity, plugin="test_plugin"):
    name: str


class _Created(Event):
    customer_id: EntityId
    name: str


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    h = Harness()
    await h.setup(entities=[_ActionCust])
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def test_action_returns_value(harness: Harness) -> None:
    class _Doubler(Action):
        value: int

        async def handle(self, uow, identity):  # type: ignore[no-untyped-def]
            return self.value * 2

    result = await harness.run(_Doubler(value=5))
    assert result == 10


async def test_action_persists_entities(harness: Harness) -> None:
    class _Create(Action):
        name: str

        async def handle(self, uow, identity):  # type: ignore[no-untyped-def]
            c = _ActionCust(name=self.name)
            await uow.save(c)
            return c

    c = await harness.run(_Create(name="Alice"))
    assert c.id is not None

    async with harness.transaction() as uow:
        loaded = await uow.get(_ActionCust, c.id)
    assert loaded.name == "Alice"


async def test_action_emits_events_atomically(harness: Harness) -> None:
    class _CreateAndEmit(Action):
        name: str

        async def handle(self, uow, identity):  # type: ignore[no-untyped-def]
            c = _ActionCust(name=self.name)
            await uow.save(c)
            uow.emit(_Created(customer_id=c.id, name=c.name))
            return c

    await harness.run(_CreateAndEmit(name="Alice"))
    assert len(await harness.events_of_type(_Created)) == 1


async def test_action_failure_rolls_back_everything(harness: Harness) -> None:
    class _Fail(Action):
        async def handle(self, uow, identity):  # type: ignore[no-untyped-def]
            c = _ActionCust(name="Alice")
            await uow.save(c)
            uow.emit(_Created(customer_id=c.id, name="Alice"))
            raise RuntimeError("simulated failure inside action")

    with pytest.raises(RuntimeError):
        await harness.run(_Fail())

    async with harness.transaction() as uow:
        assert await uow.query(_ActionCust).count() == 0
    assert len(await harness.events_of_type(_Created)) == 0


async def test_action_receives_default_identity(harness: Harness) -> None:
    received: list = []

    class _CheckIdentity(Action):
        async def handle(self, uow, identity):  # type: ignore[no-untyped-def]
            received.append(identity)

    await harness.run(_CheckIdentity())
    assert isinstance(received[0], System)


async def test_action_receives_explicit_identity(harness: Harness) -> None:
    received: list = []

    class _CheckIdentity(Action):
        async def handle(self, uow, identity):  # type: ignore[no-untyped-def]
            received.append(identity)

    user = User(user_id="alice")
    await harness.run(_CheckIdentity(), identity=user)
    assert received[0] == user


async def test_action_is_frozen() -> None:
    class _A(Action):
        value: int

        async def handle(self, uow, identity):  # type: ignore[no-untyped-def]
            return self.value

    a = _A(value=5)
    with pytest.raises(ValidationError):
        a.value = 10  # type: ignore[misc]
