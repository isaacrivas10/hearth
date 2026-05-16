"""ApiKey entity — a programmatic actor authenticated by an opaque key string.
Actor-capable."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from hearth import EntityId, References
from hearth_auth._base import Entity
from hearth_auth.entities.user import User
from hearth_auth.values import HashedSecret

if TYPE_CHECKING:
    from hearth.unit_of_work import UnitOfWork


class ApiKey(Entity):
    actor_kind: ClassVar[str] = "api_key"

    name: str
    key_prefix: str
    key_hash: HashedSecret
    owner_id: EntityId | None = None
    owner: User | None = References(User)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        """Resolve via ApiKeyPermission → Permission in a single query.

        Wildcard semantics live in `permission_grant_exists`: a grant satisfies
        the request when the grant's resource is `*` or matches, AND the grant's
        action is `*` or matches. So `Permission("orders", "*")` granted to a
        key authorizes every action on orders.

        Revoked (`revoked_at` set) or expired (`expires_at` in the past) keys
        deny everything."""
        from datetime import UTC, datetime

        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at < datetime.now(UTC):
            return False

        # Local imports to avoid a top-level cycle.
        from hearth_auth.entities.api_key_permission import ApiKeyPermission
        from hearth_auth.entities.permission import Permission
        from hearth_auth.helpers import permission_grant_exists

        grants = (
            uow.query(Permission)
            .join(ApiKeyPermission, ApiKeyPermission.permission_id == Permission.id)
            .where(ApiKeyPermission.api_key_id == self.id)
        )
        return await permission_grant_exists(grants, permission)
