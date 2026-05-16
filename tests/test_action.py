"""Tests for the Action primitive."""

from collections.abc import AsyncIterator
from typing import ClassVar

import pytest
from pydantic import ValidationError

from hearth import Entity, EntityId, Event
from hearth.primitives.action import Action
from hearth.primitives.actor import System
from hearth.testing import InMemoryHarness


class _ActionCust(Entity, plugin="test_plugin"):
    name: str


class _Created(Event):
    customer_id: EntityId
    name: str


@pytest.fixture
async def harness() -> AsyncIterator[InMemoryHarness]:
    h = InMemoryHarness()
    await h.setup(entities=[_ActionCust])
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def test_action_returns_value(harness: InMemoryHarness) -> None:
    class _Doubler(Action):
        value: int

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            return self.value * 2

    result = await harness.run(_Doubler(value=5))
    assert result == 10


async def test_action_persists_entities(harness: InMemoryHarness) -> None:
    class _Create(Action):
        name: str

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            c = _ActionCust(name=self.name)
            await uow.save(c)
            return c

    c = await harness.run(_Create(name="Alice"))
    assert c.id is not None

    async with harness.transaction() as uow:
        loaded = await uow.get(_ActionCust, c.id)
    assert loaded.name == "Alice"


async def test_action_emits_events_atomically(harness: InMemoryHarness) -> None:
    class _CreateAndEmit(Action):
        name: str

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            c = _ActionCust(name=self.name)
            await uow.save(c)
            uow.emit(_Created(customer_id=c.id, name=c.name))
            return c

    await harness.run(_CreateAndEmit(name="Alice"))
    assert len(await harness.events_of_type(_Created)) == 1


async def test_action_failure_rolls_back_everything(harness: InMemoryHarness) -> None:
    class _Fail(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            c = _ActionCust(name="Alice")
            await uow.save(c)
            uow.emit(_Created(customer_id=c.id, name="Alice"))
            raise RuntimeError("simulated failure inside action")

    with pytest.raises(RuntimeError):
        await harness.run(_Fail())

    async with harness.transaction() as uow:
        assert await uow.query(_ActionCust).count() == 0
    assert len(await harness.events_of_type(_Created)) == 0


async def test_action_receives_default_actor(harness: InMemoryHarness) -> None:
    received: list = []

    class _CheckActor(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            received.append(actor)

    await harness.run(_CheckActor())
    assert isinstance(received[0], System)


async def test_action_receives_explicit_actor(harness: InMemoryHarness) -> None:
    from hearth.primitives.actor import PluginActor

    received: list = []
    explicit = PluginActor(alias="other")

    class _CheckActor(Action):
        async def handle(self, uow, actor_arg):  # type: ignore[no-untyped-def]
            received.append(actor_arg)

    await harness.run(_CheckActor(), actor=explicit)
    assert received[0] is explicit


async def test_action_is_frozen() -> None:
    class _A(Action):
        value: int

        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            return self.value

    a = _A(value=5)
    with pytest.raises(ValidationError):
        a.value = 10  # type: ignore[misc]


async def test_outbox_actor_serialization_for_system(harness: InMemoryHarness) -> None:
    """Outbox `actor` column stores {kind, id, meta} JSON dict for System."""
    from sqlalchemy import select

    from hearth.kernel.persistence import OUTBOX_TABLE

    class _TinyEvent(Event):
        note: str

    class _Emit(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            uow.emit(_TinyEvent(note="hello"))

    await harness.run(_Emit())

    async with harness.transaction() as uow:
        result = await uow._session.execute(select(OUTBOX_TABLE.c.actor))  # pyright: ignore[reportPrivateUsage]
        actor_json = result.scalar_one()

    assert actor_json == {"kind": "system", "id": None, "meta": {}}


async def test_outbox_actor_serialization_for_plugin_actor(harness: InMemoryHarness) -> None:
    """PluginActor serializes with the alias under meta."""
    from sqlalchemy import select

    from hearth.kernel.persistence import OUTBOX_TABLE
    from hearth.primitives.actor import PluginActor

    class _AnotherEvent(Event):
        note: str

    class _Emit(Action):
        async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
            uow.emit(_AnotherEvent(note="hi"))

    await harness.run(_Emit(), actor=PluginActor(alias="commerce"))

    async with harness.transaction() as uow:
        result = await uow._session.execute(select(OUTBOX_TABLE.c.actor))  # pyright: ignore[reportPrivateUsage]
        actor_json = result.scalar_one()

    assert actor_json == {"kind": "plugin", "id": None, "meta": {"alias": "commerce"}}


# Entity-actor used only by the outbox serialization test below. Declared in
# a different plugin namespace so it doesn't collide with _ActionCust above.
class _ActorEntity(Entity, plugin="test_actor_plugin"):
    actor_kind: ClassVar[str] = "user"
    label: str

    async def has_permission(self, uow, permission):  # type: ignore[no-untyped-def]
        return True


async def test_outbox_actor_serialization_for_entity_actor() -> None:
    """An entity satisfying the Actor Protocol serializes with its `id`.

    This exercises the entity-actor branch of `_serialize_actor` — the same
    path real plugin actors (e.g., `hearth_auth.User`) take when they appear
    on the outbox row.
    """
    from sqlalchemy import select

    from hearth.kernel.persistence import OUTBOX_TABLE

    h = InMemoryHarness()
    await h.setup(entities=[_ActorEntity])
    try:
        await h.reset()

        class _EntityEvent(Event):
            note: str

        # First create the actor entity in its own transaction so its id exists.
        async with h.transaction() as uow:
            actor_entity = _ActorEntity(label="alice")
            await uow.save(actor_entity)
        actor_id = actor_entity.id

        class _Emit(Action):
            async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
                uow.emit(_EntityEvent(note="hi"))

        # Re-load inside the action's transaction so SA tracks it on the new session.
        async with h.transaction() as uow:
            fresh_actor = await uow.get(_ActorEntity, actor_id)
        await h.run(_Emit(), actor=fresh_actor)

        async with h.transaction() as uow:
            result = await uow._session.execute(select(OUTBOX_TABLE.c.actor))  # pyright: ignore[reportPrivateUsage]
            actor_json = result.scalar_one()

        assert actor_json == {"kind": "user", "id": str(actor_id), "meta": {}}
    finally:
        await h.teardown()
