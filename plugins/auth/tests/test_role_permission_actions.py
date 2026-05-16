"""Tests for Role + Permission lifecycle actions."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest
from pydantic import SecretStr

from hearth.testing import BaseHarness
from hearth_auth.actions import (
    AssignPermissionToRole,
    AssignRoleToUser,
    CreatePermission,
    CreateRole,
    CreateUser,
    RevokePermissionFromRole,
    RevokeRoleFromUser,
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
from hearth_auth.events import (
    PermissionAssignedToRole,
    PermissionCreated,
    PermissionRevokedFromRole,
    RoleAssignedToUser,
    RoleCreated,
    RoleRevokedFromUser,
)
from hearth_auth.values import PermissionName
from hearth_commons import EmailAddress

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


async def test_create_role(harness: BaseHarness) -> None:
    role = await harness.run(CreateRole(name="admin", description="The admin role."))
    assert role.name == "admin"
    events = await harness.events_of_type(RoleCreated)
    assert any(e.role_id == role.id and e.name == "admin" for e in events)


async def test_create_permission(harness: BaseHarness) -> None:
    perm = await harness.run(
        CreatePermission(name=PermissionName(resource="orders", action="read"))
    )
    assert perm.name.resource == "orders"
    events = await harness.events_of_type(PermissionCreated)
    matching = [e for e in events if e.permission_id == perm.id]
    assert len(matching) == 1
    assert matching[0].name == PermissionName(resource="orders", action="read")


async def test_assign_role_to_user(harness: BaseHarness) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="x@example.com"), password=SecretStr("p"))
    )
    role = await harness.run(CreateRole(name="admin"))
    user_role = await harness.run(AssignRoleToUser(user_id=user.id, role_id=role.id))
    assert user_role.user_id == user.id
    assert user_role.role_id == role.id
    assert len(await harness.events_of_type(RoleAssignedToUser)) == 1


async def test_revoke_role_from_user(harness: BaseHarness) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="y@example.com"), password=SecretStr("p"))
    )
    role = await harness.run(CreateRole(name="admin"))
    await harness.run(AssignRoleToUser(user_id=user.id, role_id=role.id))
    await harness.run(RevokeRoleFromUser(user_id=user.id, role_id=role.id))
    async with harness.transaction() as uow:
        count = await uow.query(UserRole).where(UserRole.user_id == user.id).count()
    assert count == 0
    assert len(await harness.events_of_type(RoleRevokedFromUser)) == 1


async def test_assign_permission_to_role(harness: BaseHarness) -> None:
    role = await harness.run(CreateRole(name="r"))
    perm = await harness.run(CreatePermission(name=PermissionName(resource="x", action="y")))
    rp = await harness.run(AssignPermissionToRole(role_id=role.id, permission_id=perm.id))
    assert rp.role_id == role.id
    assert rp.permission_id == perm.id
    assert len(await harness.events_of_type(PermissionAssignedToRole)) == 1


async def test_revoke_permission_from_role(harness: BaseHarness) -> None:
    role = await harness.run(CreateRole(name="r"))
    perm = await harness.run(CreatePermission(name=PermissionName(resource="x", action="y")))
    await harness.run(AssignPermissionToRole(role_id=role.id, permission_id=perm.id))
    await harness.run(RevokePermissionFromRole(role_id=role.id, permission_id=perm.id))
    async with harness.transaction() as uow:
        count = await uow.query(RolePermission).where(RolePermission.role_id == role.id).count()
    assert count == 0
    assert len(await harness.events_of_type(PermissionRevokedFromRole)) == 1
