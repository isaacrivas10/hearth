"""Tests for AuthenticateUser. Timing-constant behavior is asserted by
counting verify() invocations rather than wall-clock time."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from hearth.testing import BaseHarness
from hearth_auth import AuthenticationFailed
from hearth_auth.actions import AuthenticateUser, CreateUser, DisableUser
from hearth_auth.entities import (
    ApiKey,
    ApiKeyPermission,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from hearth_auth.events import UserAuthenticated
from hearth_auth.values import HashedSecret
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


async def test_authenticate_user_success(harness: BaseHarness) -> None:
    created = await harness.run(
        CreateUser(email=EmailAddress(raw="alice@example.com"), password=SecretStr("pw"))
    )
    authed = await harness.run(
        AuthenticateUser(email=EmailAddress(raw="alice@example.com"), password=SecretStr("pw"))
    )
    assert authed.id == created.id
    events = await harness.events_of_type(UserAuthenticated)
    assert any(e.user_id == created.id for e in events)


async def test_authenticate_user_wrong_password(harness: BaseHarness) -> None:
    await harness.run(
        CreateUser(email=EmailAddress(raw="bob@example.com"), password=SecretStr("right"))
    )
    with pytest.raises(AuthenticationFailed):
        await harness.run(
            AuthenticateUser(email=EmailAddress(raw="bob@example.com"), password=SecretStr("wrong"))
        )


async def test_authenticate_user_unknown_email(harness: BaseHarness) -> None:
    with pytest.raises(AuthenticationFailed):
        await harness.run(
            AuthenticateUser(
                email=EmailAddress(raw="ghost@example.com"),
                password=SecretStr("anything"),
            )
        )


async def test_authenticate_user_disabled(harness: BaseHarness) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="carol@example.com"), password=SecretStr("pw"))
    )
    await harness.run(DisableUser(user_id=user.id))
    with pytest.raises(AuthenticationFailed):
        await harness.run(
            AuthenticateUser(email=EmailAddress(raw="carol@example.com"), password=SecretStr("pw"))
        )


async def test_authenticate_user_timing_runs_verify_for_unknown_email(
    harness: BaseHarness,
) -> None:
    """Timing-constant requirement: even when the email is not found,
    AuthenticateUser MUST still invoke a verify() call (against the
    sentinel) so an attacker cannot enumerate by response time."""
    call_count = {"n": 0}

    real_verify = HashedSecret.verify

    def counted(self: HashedSecret, plaintext) -> bool:  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        return real_verify(self, plaintext)

    with patch.object(HashedSecret, "verify", counted), pytest.raises(AuthenticationFailed):
        await harness.run(
            AuthenticateUser(
                email=EmailAddress(raw="never-existed@example.com"),
                password=SecretStr("anything"),
            )
        )
    # `>= 1` is necessary but not sufficient for timing constancy. The actual
    # defense lives in AuthenticateUser.handle: a sentinel HashedSecret is
    # substituted when no user row matches, so verify() runs on every branch.
    # This test catches regressions that delete that substitution.
    assert call_count["n"] >= 1


async def test_authenticate_user_updates_last_login_at(harness: BaseHarness) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="dave@example.com"), password=SecretStr("pw"))
    )
    assert user.last_login_at is None
    authed = await harness.run(
        AuthenticateUser(email=EmailAddress(raw="dave@example.com"), password=SecretStr("pw"))
    )
    assert authed.last_login_at is not None
