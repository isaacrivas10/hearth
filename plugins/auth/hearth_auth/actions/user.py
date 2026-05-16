"""User-lifecycle actions: Create, ChangePassword, Disable, Enable.
AuthenticateUser is in actions/user_auth.py."""

from __future__ import annotations

import asyncio

from pydantic import SecretStr

from hearth import Actor, EntityId, UnitOfWork
from hearth_auth._base import Action
from hearth_auth.entities.user import User
from hearth_auth.events.user import (
    PasswordChanged,
    UserCreated,
    UserDisabled,
    UserEnabled,
)
from hearth_auth.helpers import AuthenticationFailed
from hearth_auth.values import HashedSecret
from hearth_commons import EmailAddress


class CreateUser(Action):
    email: EmailAddress
    password: SecretStr
    display_name: str | None = None

    async def handle(self, uow: UnitOfWork, actor: Actor) -> User:
        hashed = await asyncio.to_thread(HashedSecret.from_plaintext, self.password)
        user = User(
            email=self.email,
            password=hashed,
            display_name=self.display_name,
        )
        await uow.save(user)
        assert user.id is not None
        uow.emit(UserCreated(user_id=user.id, email=self.email))
        return user


class ChangePassword(Action):
    user_id: EntityId
    old_password: SecretStr
    new_password: SecretStr

    async def handle(self, uow: UnitOfWork, actor: Actor) -> None:
        user = await uow.query(User).where(User.id == self.user_id).one_or_none()
        if user is None:
            raise AuthenticationFailed("invalid credentials")
        ok = await asyncio.to_thread(user.password.verify, self.old_password)
        if not ok:
            raise AuthenticationFailed("invalid credentials")
        new_hashed = await asyncio.to_thread(HashedSecret.from_plaintext, self.new_password)
        user.password = new_hashed
        await uow.save(user)
        uow.emit(PasswordChanged(user_id=user.id))


class DisableUser(Action):
    user_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Actor) -> None:
        user = await uow.query(User).where(User.id == self.user_id).one()
        user.is_active = False
        await uow.save(user)
        uow.emit(UserDisabled(user_id=user.id))


class EnableUser(Action):
    user_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Actor) -> None:
        user = await uow.query(User).where(User.id == self.user_id).one()
        user.is_active = True
        await uow.save(user)
        uow.emit(UserEnabled(user_id=user.id))
