"""Test helpers for plugins that depend on hearth-auth in their own test suites.

Plugin authors import these to seed admin users and grant permissions inside
their test fixtures, without going through the full CLI flow."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import SecretStr

from hearth import EntityId
from hearth.testing import BaseHarness
from hearth_auth.actions import (
    AssignPermissionToRole,
    AssignRoleToUser,
    CreatePermission,
    CreateRole,
    CreateUser,
)
from hearth_auth.entities import User
from hearth_auth.values import PermissionName
from hearth_commons import EmailAddress


async def seed_admin(
    harness: BaseHarness,
    email: str = "admin@x.com",
    password: str = "adminpass",
) -> User:
    """Programmatic equivalent of `hearth auth bootstrap`. Creates a User
    with the wildcard '*:*' permission via an 'admin' Role."""
    user = await harness.run(
        CreateUser(
            email=EmailAddress(raw=email),
            password=SecretStr(password),
            display_name="Admin",
        )
    )
    role = await harness.run(CreateRole(name="admin", description="Bootstrapped admin."))
    wildcard = await harness.run(
        CreatePermission(
            name=PermissionName(resource="*", action="*"),
            description="Wildcard — grants all permissions.",
        )
    )
    await harness.run(AssignPermissionToRole(role_id=role.id, permission_id=wildcard.id))
    await harness.run(AssignRoleToUser(user_id=user.id, role_id=role.id))
    return user


async def grant_permissions(
    harness: BaseHarness,
    role_id: EntityId,
    permissions: Iterable[PermissionName],
) -> None:
    """Bulk-create Permission rows and attach to a Role. Test convenience."""
    for pn in permissions:
        perm = await harness.run(CreatePermission(name=pn))
        await harness.run(AssignPermissionToRole(role_id=role_id, permission_id=perm.id))
