"""User entity — a human account with login credentials. Actor-capable."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from hearth_auth._base import Entity
from hearth_auth.values import HashedSecret
from hearth_commons import EmailAddress

if TYPE_CHECKING:
    from hearth.unit_of_work import UnitOfWork


class User(Entity):
    actor_kind: ClassVar[str] = "user"

    email: EmailAddress
    password: HashedSecret
    display_name: str | None = None
    is_active: bool = True
    last_login_at: datetime | None = None

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        """Resolve via UserRole → RolePermission → Permission in a single query.

        Wildcard semantics live in `permission_grant_exists`: a grant satisfies
        the request when the grant's resource is `*` or matches, AND the grant's
        action is `*` or matches. So `Permission("orders", "*")` grants every
        action on orders, `Permission("*", "read")` grants read on every
        resource, and `Permission("*", "*")` grants everything.

        Disabled users (`is_active=False`) deny everything, mirroring the
        `revoked_at`/`expires_at` checks on `ApiKey`. This is defense in depth
        against actors captured at auth time and reused after the user was
        disabled — without it, a disabled user passed via a long-lived session
        or queued job would still satisfy `@requires` until re-authentication.
        """
        if not self.is_active:
            return False

        # Local imports to avoid a top-level cycle.
        from hearth_auth.entities.permission import Permission
        from hearth_auth.entities.role_permission import RolePermission
        from hearth_auth.entities.user_role import UserRole
        from hearth_auth.helpers import permission_grant_exists

        grants = (
            uow.query(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .where(UserRole.user_id == self.id)
        )
        return await permission_grant_exists(grants, permission)
