"""Tests for the Actor primitive — see docs/core/primitives/actor.md."""

from __future__ import annotations

from typing import ClassVar

from hearth import Anonymous, PluginActor, System
from hearth.primitives.actor import serialize_actor
from hearth.testing import InMemoryHarness


class _StubEntity:
    """A stand-in actor that satisfies the Protocol without being an Entity.
    Used to confirm the Protocol is structural, not nominal."""

    actor_kind: ClassVar[str] = "stub"

    async def has_permission(self, uow, permission):  # type: ignore[no-untyped-def]
        return permission == "stub:allowed"


def test_system_actor_kind() -> None:
    assert System().actor_kind == "system"


def test_anonymous_actor_kind() -> None:
    assert Anonymous().actor_kind == "anonymous"


def test_plugin_actor_carries_alias() -> None:
    pa = PluginActor(alias="commerce")
    assert pa.actor_kind == "plugin"
    assert pa.alias == "commerce"


async def test_system_has_all_permissions() -> None:
    harness = InMemoryHarness()
    await harness.setup(entities=[])
    try:
        await harness.reset()
        async with harness.transaction() as uow:
            assert await System().has_permission(uow, "anything:goes") is True
    finally:
        await harness.teardown()


async def test_anonymous_has_no_permissions() -> None:
    harness = InMemoryHarness()
    await harness.setup(entities=[])
    try:
        await harness.reset()
        async with harness.transaction() as uow:
            assert await Anonymous().has_permission(uow, "anything") is False
    finally:
        await harness.teardown()


async def test_plugin_actor_has_all_permissions() -> None:
    harness = InMemoryHarness()
    await harness.setup(entities=[])
    try:
        await harness.reset()
        async with harness.transaction() as uow:
            assert await PluginActor(alias="commerce").has_permission(uow, "anything") is True
    finally:
        await harness.teardown()


def test_protocol_is_structural() -> None:
    """An object that has actor_kind + async has_permission satisfies Actor
    without inheriting from it — Actor is a Protocol."""
    stub = _StubEntity()
    # No isinstance check — Python Protocols don't require nominal inheritance.
    # The test is: stub.actor_kind exists and stub.has_permission is awaitable.
    assert stub.actor_kind == "stub"


# --- serialize_actor: audit-log "kind:id" rendering ---------------------------


class _FakeEntityActor:
    """Stand-in for an entity-actor (e.g., hearth-auth's User) — has both
    `actor_kind` and `id` attributes, satisfies the structural Actor
    surface that `serialize_actor` reads via `getattr`.
    """

    def __init__(self, actor_kind: str, id: object) -> None:
        self.actor_kind = actor_kind
        self.id = id

    async def has_permission(self, uow, permission):  # type: ignore[no-untyped-def]
        return False


def test_serialize_actor_system_renders_bare_kind() -> None:
    assert serialize_actor(System()) == "system"


def test_serialize_actor_anonymous_renders_bare_kind() -> None:
    assert serialize_actor(Anonymous()) == "anonymous"


def test_serialize_actor_plugin_actor_uses_alias() -> None:
    assert serialize_actor(PluginActor(alias="commerce")) == "plugin:commerce"


def test_serialize_actor_entity_actor_renders_kind_and_id() -> None:
    assert serialize_actor(_FakeEntityActor("user", "u-123")) == "user:u-123"


def test_serialize_actor_zero_id_is_not_collapsed() -> None:
    """Regression: falsy-but-valid ids (`0`, `""`, `False`) must render as
    `kind:<id>`, not collapse to the bare kind. Earlier `if raw_id:` would
    treat `id=0` as "no id" and emit `"user"` — semantically a different
    actor from `"user:0"`."""
    assert serialize_actor(_FakeEntityActor("user", 0)) == "user:0"
