"""Proves that @requires from hearth_auth correctly gates actions registered
by another plugin — i.e., the cross-plugin authorization story works.

The "other plugin" is constructed inline as a fake module with a single
@requires-decorated action; nothing is registered via entry points."""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator, Callable

import pytest
from pydantic import SecretStr

from hearth import bases_for
from hearth.testing import BaseHarness
from hearth_auth import PermissionDenied, requires
from hearth_auth.actions import (
    AssignPermissionToRole,
    AssignRoleToUser,
    CreatePermission,
    CreateRole,
    CreateUser,
)
from hearth_auth.entities import (
    ApiKey,
    ApiKeyPermission,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from hearth_auth.testing import seed_admin
from hearth_auth.values import PermissionName
from hearth_commons import EmailAddress

# Build a fake 'fake_orders_cross' plugin once, at module import time. The module
# name uses the standard `hearth_test_<scenario>` convention; the action is
# attached to it so the entity-metaclass can resolve string annotations if
# any are added later.
_FAKE_MOD_NAME = "hearth_test_fake_orders_cross"
_fake_mod = types.ModuleType(_FAKE_MOD_NAME)
sys.modules[_FAKE_MOD_NAME] = _fake_mod
_, _FakeAction, _ = bases_for("fake_orders_cross")


@requires("orders:read")
class ReadOrders(_FakeAction):  # type: ignore[misc]
    async def handle(self, uow, actor):  # type: ignore[no-untyped-def]
        return "orders-data"


ReadOrders.__module__ = _FAKE_MOD_NAME
_fake_mod.ReadOrders = ReadOrders


ENTITY_LIST = [User, Role, Permission, UserRole, RolePermission, ApiKey, ApiKeyPermission]


@pytest.fixture
async def harness(
    make_harness: Callable[[], BaseHarness],
) -> AsyncIterator[BaseHarness]:
    h = make_harness()
    await h.setup(entities=ENTITY_LIST)
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def test_admin_via_wildcard_can_run_cross_plugin_action(
    harness: BaseHarness,
) -> None:
    admin = await seed_admin(harness)
    result = await harness.run(ReadOrders(), actor=admin)
    assert result == "orders-data"


async def test_user_without_grant_cannot_run_cross_plugin_action(
    harness: BaseHarness,
) -> None:
    bare = await harness.run(
        CreateUser(email=EmailAddress(raw="bare@x.com"), password=SecretStr("p"))
    )
    with pytest.raises(PermissionDenied):
        await harness.run(ReadOrders(), actor=bare)


async def test_user_with_specific_grant_can_run_cross_plugin_action(
    harness: BaseHarness,
) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="reader@x.com"), password=SecretStr("p"))
    )
    role = await harness.run(CreateRole(name="reader"))
    perm = await harness.run(
        CreatePermission(name=PermissionName(resource="orders", action="read"))
    )
    await harness.run(AssignPermissionToRole(role_id=role.id, permission_id=perm.id))
    await harness.run(AssignRoleToUser(user_id=user.id, role_id=role.id))

    result = await harness.run(ReadOrders(), actor=user)
    assert result == "orders-data"
