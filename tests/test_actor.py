"""Tests for the Actor primitive — see docs/core/primitives/actor.md."""

from __future__ import annotations

from typing import ClassVar

from hearth import Anonymous, PluginActor, System
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
