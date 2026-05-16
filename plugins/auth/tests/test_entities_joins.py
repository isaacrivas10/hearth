"""Tests for join entities and ApiKey."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest

from hearth.testing import BaseHarness
from hearth_auth.entities import (
    ApiKey,
    ApiKeyPermission,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from hearth_auth.values import HashedSecret, PermissionName
from hearth_commons import EmailAddress


@pytest.fixture
async def harness(
    make_harness: Callable[[], BaseHarness],
) -> AsyncIterator[BaseHarness]:
    h = make_harness()
    await h.setup(
        entities=[
            User,
            Role,
            Permission,
            UserRole,
            RolePermission,
            ApiKey,
            ApiKeyPermission,
        ]
    )
    try:
        await h.reset()
        yield h
    finally:
        await h.teardown()


async def test_user_role_round_trip(harness: BaseHarness) -> None:
    async with harness.transaction() as uow:
        u = User(
            email=EmailAddress(raw="alice@example.com"),
            password=HashedSecret.from_plaintext("p"),
        )
        await uow.save(u)
        r = Role(name="admin")
        await uow.save(r)
        ur = UserRole(user=u, role=r)
        await uow.save(ur)
        assert ur.granted_at is not None
        assert ur.granted_by_id is None


async def test_role_permission_round_trip(harness: BaseHarness) -> None:
    async with harness.transaction() as uow:
        r = Role(name="admin")
        await uow.save(r)
        p = Permission(name=PermissionName(resource="orders", action="read"))
        await uow.save(p)
        rp = RolePermission(role=r, permission=p)
        await uow.save(rp)


async def test_api_key_round_trip(harness: BaseHarness) -> None:
    async with harness.transaction() as uow:
        k = ApiKey(
            name="webhook-tigo",
            key_prefix="hak_abc12",
            key_hash=HashedSecret.from_plaintext("hak_abc12345_long_secret"),
            expires_at=None,
            revoked_at=None,
        )
        await uow.save(k)
        assert k.id is not None
        assert k.actor_kind == "api_key"


async def test_api_key_permission_round_trip(harness: BaseHarness) -> None:
    async with harness.transaction() as uow:
        k = ApiKey(
            name="webhook",
            key_prefix="hak_xyz",
            key_hash=HashedSecret.from_plaintext("hak_xyz_secret"),
            expires_at=None,
            revoked_at=None,
        )
        await uow.save(k)
        p = Permission(name=PermissionName(resource="webhooks", action="receive"))
        await uow.save(p)
        akp = ApiKeyPermission(api_key=k, permission=p)
        await uow.save(akp)
