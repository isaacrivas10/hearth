"""Auth-plugin helpers: enforcement decorator + imperative check + exceptions."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from hearth import Action, Actor, Query, and_, or_
from hearth.unit_of_work import UnitOfWork

if TYPE_CHECKING:
    from hearth_auth.entities.permission import Permission


class AuthenticationFailed(Exception):  # noqa: N818
    """Raised by AuthenticateUser / AuthenticateApiKey on bad/missing/expired credentials.
    The message is intentionally generic ("invalid credentials") to avoid
    leaking which branch (no user vs wrong password vs disabled) failed."""


class PermissionDenied(Exception):  # noqa: N818
    """Raised by require_permission and the @requires decorator when an actor
    lacks the requested permission."""

    def __init__(self, actor: Actor, permission: str) -> None:
        self.actor = actor
        self.permission = permission
        super().__init__(f"actor of kind {actor.actor_kind!r} denied permission {permission!r}")


class PermissionNotFound(Exception):  # noqa: N818
    """Raised by CreateApiKey when a referenced PermissionName doesn't match
    any existing Permission row. Forces operators to create permissions
    explicitly (via CreatePermission) rather than auto-creating from typos."""

    def __init__(self, names: list[Any]) -> None:
        self.names = names
        super().__init__(
            f"the following PermissionNames are not registered: {names!r}. "
            f"Create them via CreatePermission first."
        )


async def permission_grant_exists(query: Query[Permission], permission: str) -> bool:
    """Apply the wildcard-aware predicate to a permission query and check existence.

    The caller supplies a Query already joined to Permission through the appropriate
    grant chain (User → UserRole → RolePermission → Permission, or
    ApiKey → ApiKeyPermission → Permission) and filtered to the actor's own grants.

    Resolution semantics — a grant satisfies the request when BOTH:
      - the grant's resource is `*` or equals the requested resource, AND
      - the grant's action is `*` or equals the requested action.

    This means `Permission("orders", "*")` grants every action on orders,
    `Permission("*", "read")` grants the read action on every resource, and
    `Permission("*", "*")` grants everything. Specific-only grants never
    escalate to a wildcard request: a user with `("orders", "read")` does
    NOT satisfy `has_permission("*:*")`.

    Defends in depth against malformed permission strings: requires a single
    `:` separator and rejects empty/whitespace-only resource or action segments.
    PermissionName's own validator already prevents empty grants from being
    persisted; this second check ensures a malformed *query* can't match an
    incidentally-empty row even if one exists.
    """
    # Local import to avoid a top-level cycle (entities import from helpers
    # transitively via Action subclasses).
    from hearth_auth.entities.permission import Permission

    if permission.count(":") != 1:
        return False
    resource, action = permission.split(":", 1)
    if not resource.strip() or not action.strip():
        return False
    return await query.where(
        and_(
            or_(Permission.name.resource == "*", Permission.name.resource == resource),
            or_(Permission.name.action == "*", Permission.name.action == action),
        ),
    ).exists()


async def require_permission(uow: UnitOfWork, actor: Actor, permission: str) -> None:
    """Raise PermissionDenied if `actor` lacks `permission`. No-op on success."""
    if not await actor.has_permission(uow, permission):
        raise PermissionDenied(actor, permission)


def requires(*permissions: str) -> Callable[[type[Action]], type[Action]]:
    """Decorator: gate an Action class on static permissions.

    Wraps the class's `handle` so every listed permission is checked before
    the body runs. For dynamic checks (e.g., row-level ownership), call
    `require_permission` inside the body instead.

    LIMITATION: the decorator wraps `cls.handle` at decoration time. A
    subclass that overrides `handle` REPLACES the wrapped version and
    bypasses the gate silently. Plugin authors who subclass a `@requires`-
    decorated Action MUST reapply `@requires(...)` on the subclass.

    Side effect: sets `_hearth_required_permissions: tuple[str, ...]`
    on the class for test helpers and admin tools to introspect.
    """

    def wrap(cls: type[Action]) -> type[Action]:
        cls._hearth_required_permissions = permissions  # type: ignore[attr-defined]
        original_handle = cls.handle

        @functools.wraps(original_handle)
        async def handle(self: Action, uow: UnitOfWork, actor: Actor) -> Any:
            for p in permissions:
                await require_permission(uow, actor, p)
            return await original_handle(self, uow, actor)

        cls.handle = handle  # type: ignore[method-assign]
        return cls

    return wrap
