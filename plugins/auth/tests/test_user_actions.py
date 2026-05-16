"""Tests for user lifecycle actions (excluding AuthenticateUser — that's its own file)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest
from pydantic import SecretStr

from hearth.testing import BaseHarness
from hearth_auth.actions import ChangePassword, CreateUser, DisableUser, EnableUser
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
    PasswordChanged,
    UserCreated,
    UserDisabled,
    UserEnabled,
)
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


async def test_create_user_persists_and_hashes(harness: BaseHarness) -> None:
    user = await harness.run(
        CreateUser(
            email=EmailAddress(raw="alice@example.com"),
            password=SecretStr("hunter2"),
            display_name="Alice",
        )
    )
    assert isinstance(user, User)
    assert user.password.hash.startswith("$argon2id$")
    assert user.password.verify("hunter2")

    events = await harness.events_of_type(UserCreated)
    assert len(events) == 1
    assert events[0].user_id == user.id
    assert events[0].email == EmailAddress(raw="alice@example.com")


async def test_change_password_succeeds_with_correct_old(harness: BaseHarness) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="bob@example.com"), password=SecretStr("old"))
    )
    await harness.run(
        ChangePassword(
            user_id=user.id, old_password=SecretStr("old"), new_password=SecretStr("new")
        )
    )
    async with harness.transaction() as uow:
        fresh = await uow.query(User).where(User.id == user.id).one()
        assert fresh.password.verify("new")
        assert not fresh.password.verify("old")
    events = await harness.events_of_type(PasswordChanged)
    assert len(events) == 1
    assert events[0].user_id == user.id


async def test_change_password_rejects_wrong_old(harness: BaseHarness) -> None:
    from hearth_auth import AuthenticationFailed

    user = await harness.run(
        CreateUser(email=EmailAddress(raw="carol@example.com"), password=SecretStr("right"))
    )
    with pytest.raises(AuthenticationFailed):
        await harness.run(
            ChangePassword(
                user_id=user.id,
                old_password=SecretStr("wrong"),
                new_password=SecretStr("new"),
            )
        )


async def test_disable_and_enable_user(harness: BaseHarness) -> None:
    user = await harness.run(
        CreateUser(email=EmailAddress(raw="dave@example.com"), password=SecretStr("x"))
    )
    await harness.run(DisableUser(user_id=user.id))
    async with harness.transaction() as uow:
        fresh = await uow.query(User).where(User.id == user.id).one()
        assert fresh.is_active is False
    assert len(await harness.events_of_type(UserDisabled)) == 1

    await harness.run(EnableUser(user_id=user.id))
    async with harness.transaction() as uow:
        fresh = await uow.query(User).where(User.id == user.id).one()
        assert fresh.is_active is True
    assert len(await harness.events_of_type(UserEnabled)) == 1
