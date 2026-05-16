"""Tests for ApiKey lifecycle and authentication actions."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from hearth.testing import BaseHarness
from hearth_auth import AuthenticationFailed, PermissionNotFound
from hearth_auth.actions import (
    AuthenticateApiKey,
    CreateApiKey,
    CreatePermission,
    RevokeApiKey,
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
    ApiKeyAuthenticated,
    ApiKeyCreated,
    ApiKeyRevoked,
)
from hearth_auth.values import HashedSecret, PermissionName

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


async def test_create_api_key_returns_plaintext_once(harness: BaseHarness) -> None:
    await harness.run(CreatePermission(name=PermissionName(resource="webhooks", action="receive")))
    api_key, plaintext = await harness.run(
        CreateApiKey(
            name="tigo-webhook",
            permissions=[PermissionName(resource="webhooks", action="receive")],
        )
    )
    assert plaintext.startswith("hak_")
    assert api_key.key_prefix == plaintext[:8]
    assert api_key.key_hash.verify(plaintext)
    assert len(await harness.events_of_type(ApiKeyCreated)) == 1


async def test_create_api_key_rejects_unknown_permission(harness: BaseHarness) -> None:
    with pytest.raises(PermissionNotFound):
        await harness.run(
            CreateApiKey(
                name="bad",
                permissions=[PermissionName(resource="ghost", action="haunt")],
            )
        )


async def test_authenticate_api_key_success(harness: BaseHarness) -> None:
    await harness.run(CreatePermission(name=PermissionName(resource="webhooks", action="receive")))
    api_key, plaintext = await harness.run(
        CreateApiKey(
            name="k",
            permissions=[PermissionName(resource="webhooks", action="receive")],
        )
    )
    authed = await harness.run(AuthenticateApiKey(key_string=SecretStr(plaintext)))
    assert authed.id == api_key.id
    assert len(await harness.events_of_type(ApiKeyAuthenticated)) == 1


async def test_authenticate_api_key_unknown_key(harness: BaseHarness) -> None:
    with pytest.raises(AuthenticationFailed):
        await harness.run(AuthenticateApiKey(key_string=SecretStr("hak_nopealla_long")))


async def test_authenticate_api_key_revoked(harness: BaseHarness) -> None:
    await harness.run(CreatePermission(name=PermissionName(resource="x", action="y")))
    api_key, plaintext = await harness.run(
        CreateApiKey(name="k", permissions=[PermissionName(resource="x", action="y")])
    )
    await harness.run(RevokeApiKey(api_key_id=api_key.id))
    with pytest.raises(AuthenticationFailed):
        await harness.run(AuthenticateApiKey(key_string=SecretStr(plaintext)))
    assert len(await harness.events_of_type(ApiKeyRevoked)) == 1


async def test_authenticate_api_key_expired(harness: BaseHarness) -> None:
    past = datetime.now(UTC) - timedelta(days=1)
    await harness.run(CreatePermission(name=PermissionName(resource="x", action="y")))
    _, plaintext = await harness.run(
        CreateApiKey(
            name="k",
            permissions=[PermissionName(resource="x", action="y")],
            expires_at=past,
        )
    )
    with pytest.raises(AuthenticationFailed):
        await harness.run(AuthenticateApiKey(key_string=SecretStr(plaintext)))


async def test_authenticate_api_key_timing_runs_verify_on_unknown(
    harness: BaseHarness,
) -> None:
    call_count = {"n": 0}
    real_verify = HashedSecret.verify

    def counted(self, plaintext):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        return real_verify(self, plaintext)

    with patch.object(HashedSecret, "verify", counted), pytest.raises(AuthenticationFailed):
        await harness.run(AuthenticateApiKey(key_string=SecretStr("hak_unknown_long_secret")))
    # `>= 1` is necessary but not sufficient for timing constancy. The actual
    # defense lives in AuthenticateApiKey.handle: when no candidates match
    # the prefix, verify() still runs against a sentinel. This test catches
    # regressions that delete that fallback.
    assert call_count["n"] >= 1
