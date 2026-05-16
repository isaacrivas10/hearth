"""AuthenticateUser action with timing-constant flow.

The sentinel HashedSecret ensures we always run verify() once regardless of
whether the email lookup succeeded — preventing email enumeration via
response-time analysis."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from pydantic import SecretStr

from hearth import Actor, UnitOfWork
from hearth_auth._base import Action
from hearth_auth.entities.user import User
from hearth_auth.events.user import UserAuthenticated
from hearth_auth.helpers import AuthenticationFailed
from hearth_auth.values import HashedSecret
from hearth_commons import EmailAddress

# Sentinel hashed lazily on first authentication. argon2id is intentionally
# slow (~100ms); hashing at import time would tax every `hearth` CLI
# invocation that loads the auth package (e.g., `hearth db init`). The
# sentinel is used to equalize timing when the email is not found — verify()
# always runs at least once.
_sentinel_hashed: HashedSecret | None = None


async def _get_sentinel_hashed() -> HashedSecret:
    global _sentinel_hashed
    if _sentinel_hashed is None:
        _sentinel_hashed = await asyncio.to_thread(
            HashedSecret.from_plaintext, "sentinel-not-a-real-password"
        )
    return _sentinel_hashed


class AuthenticateUser(Action):
    email: EmailAddress
    password: SecretStr

    async def handle(self, uow: UnitOfWork, actor: Actor) -> User:
        user = await uow.query(User).where(User.email == self.email).one_or_none()
        target = user.password if user is not None else await _get_sentinel_hashed()
        ok = await asyncio.to_thread(target.verify, self.password)
        # All failure branches produce the same exception with the same message
        # — no information leaks via error type or text.
        if user is None or not ok or not user.is_active:
            raise AuthenticationFailed("invalid credentials")
        user.last_login_at = datetime.now(UTC)
        await uow.save(user)
        uow.emit(UserAuthenticated(user_id=user.id))
        return user
