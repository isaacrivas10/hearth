"""ApiKey-lifecycle and authentication actions."""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime

from pydantic import SecretStr

from hearth import Actor, EntityId, UnitOfWork
from hearth_auth._base import Action
from hearth_auth.entities.api_key import ApiKey
from hearth_auth.entities.api_key_permission import ApiKeyPermission
from hearth_auth.entities.permission import Permission
from hearth_auth.entities.user import User
from hearth_auth.events.api_key import (
    ApiKeyAuthenticated,
    ApiKeyCreated,
    ApiKeyRevoked,
)
from hearth_auth.helpers import AuthenticationFailed, PermissionNotFound
from hearth_auth.values import HashedSecret, PermissionName

# Sentinel hashed lazily on first authentication. argon2id is intentionally
# slow (~100ms); hashing at import time would tax every `hearth` CLI
# invocation that loads the auth package. Used for timing-constant flow when
# no candidate ApiKey row matches the prefix.
_sentinel_hashed: HashedSecret | None = None


async def _get_sentinel_hashed() -> HashedSecret:
    global _sentinel_hashed
    if _sentinel_hashed is None:
        _sentinel_hashed = await asyncio.to_thread(
            HashedSecret.from_plaintext, "sentinel-not-a-real-key"
        )
    return _sentinel_hashed


_KEY_PREFIX_LENGTH = 8


def _generate_key_string() -> str:
    """Generate a plaintext API key: 'hak_' + url-safe random tail."""
    return "hak_" + secrets.token_urlsafe(24)


class CreateApiKey(Action):
    name: str
    permissions: list[PermissionName]
    owner_id: EntityId | None = None
    expires_at: datetime | None = None

    async def handle(self, uow: UnitOfWork, actor: Actor) -> tuple[ApiKey, str]:
        # Look up Permission rows for the requested names. Fail loudly on
        # any unknown name — auto-creating would mask typos.
        # Strategy: pre-filter by resource (Query.in_ is portable), then
        # filter by action in Python. Plugins must not import SQLAlchemy
        # directly (ADR-0006); the kernel Query API is the only persistence
        # interface plugins may touch.
        if self.permissions:
            requested = {(pn.resource, pn.action) for pn in self.permissions}
            resources = list({pn.resource for pn in self.permissions})
            candidates = await (
                uow.query(Permission)
                .where(Permission.name.resource.in_(resources))  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                .all()
            )
            found = [p for p in candidates if (p.name.resource, p.name.action) in requested]
            found_tuples = {(p.name.resource, p.name.action) for p in found}
            missing = [
                pn for pn in self.permissions if (pn.resource, pn.action) not in found_tuples
            ]
            if missing:
                raise PermissionNotFound(missing)
        else:
            found = []

        plaintext = _generate_key_string()
        prefix = plaintext[:_KEY_PREFIX_LENGTH]
        hashed = await asyncio.to_thread(HashedSecret.from_plaintext, plaintext)

        # Optional owner — fetch the User object only if requested. ApiKey
        # FK columns are init=False; use the `owner` relationship attr.
        owner: User | None = None
        if self.owner_id is not None:
            owner = await uow.query(User).where(User.id == self.owner_id).one()

        api_key = ApiKey(
            name=self.name,
            key_prefix=prefix,
            key_hash=hashed,
            owner=owner,
            expires_at=self.expires_at,
        )
        await uow.save(api_key)
        assert api_key.id is not None

        # Each ApiKeyPermission is a join; construct via relationship attrs.
        for perm in found:
            await uow.save(ApiKeyPermission(api_key=api_key, permission=perm))

        uow.emit(
            ApiKeyCreated(
                api_key_id=api_key.id,
                key_prefix=prefix,
                owner_id=self.owner_id,
            )
        )
        return api_key, plaintext


class RevokeApiKey(Action):
    api_key_id: EntityId

    async def handle(self, uow: UnitOfWork, actor: Actor) -> None:
        api_key = await uow.query(ApiKey).where(ApiKey.id == self.api_key_id).one()
        api_key.revoked_at = datetime.now(UTC)
        await uow.save(api_key)
        uow.emit(ApiKeyRevoked(api_key_id=api_key.id))


class AuthenticateApiKey(Action):
    key_string: SecretStr

    async def handle(self, uow: UnitOfWork, actor: Actor) -> ApiKey:
        plaintext = self.key_string.get_secret_value()
        prefix = plaintext[:_KEY_PREFIX_LENGTH]
        candidates = await uow.query(ApiKey).where(ApiKey.key_prefix == prefix).all()

        # Always run verify at least once to equalize timing — against the
        # sentinel if no candidates exist.
        matched: ApiKey | None = None
        if not candidates:
            sentinel = await _get_sentinel_hashed()
            await asyncio.to_thread(sentinel.verify, plaintext)
        else:
            for cand in candidates:
                ok = await asyncio.to_thread(cand.key_hash.verify, plaintext)
                if ok and matched is None:
                    matched = cand
                    # Don't break — keep timing roughly uniform across the
                    # candidate set (in practice usually 0 or 1).

        if matched is None:
            raise AuthenticationFailed("invalid api key")
        if matched.revoked_at is not None:
            raise AuthenticationFailed("invalid api key")
        if matched.expires_at is not None and matched.expires_at < datetime.now(UTC):
            raise AuthenticationFailed("invalid api key")

        uow.emit(ApiKeyAuthenticated(api_key_id=matched.id))
        return matched
