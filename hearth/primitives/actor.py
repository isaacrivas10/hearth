"""Actor primitive — see docs/core/primitives/actor.md.

An Actor is the 'who' of an Action. Concrete actors are either Entities
contributed by plugins (e.g., hearth-auth's User and ApiKey) or
dataless types shipped by the kernel (System, Anonymous, PluginActor).
Both satisfy the structural Protocol below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:
    from hearth.unit_of_work import UnitOfWork


@runtime_checkable
class Actor(Protocol):
    """The 'who' of an Action. Threads through actions for audit + authz.

    actor_kind convention: snake_case, lowercase. Single words for the kernel
    types (`"system"`, `"anonymous"`, `"plugin"`); multi-word actor types
    use underscore (`"api_key"`). The string is the wire-level discriminator
    for outbox audit JSON; choose carefully — once shipped it shouldn't be
    renamed.
    """

    actor_kind: ClassVar[str]

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        """Return True if this actor holds the given permission string.

        Permission strings follow the `<resource>:<action>` convention.
        Resolution semantics are the actor's responsibility — the kernel
        knows nothing about how permissions are stored or granted.
        """
        ...


@dataclass(frozen=True)
class System:
    """The kernel itself — outbox dispatch, bootstrap, schema operations."""

    actor_kind: ClassVar[str] = "system"

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        return True  # kernel-internal callers are trusted


@dataclass(frozen=True)
class Anonymous:
    """Unauthenticated public access. Used by transport layers for endpoints
    that explicitly allow no auth."""

    actor_kind: ClassVar[str] = "anonymous"

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        return False


@dataclass(frozen=True)
class PluginActor:
    """A plugin acting on its own behalf — e.g., a scheduled cron from the
    commerce plugin running nightly invoice generation.

    SECURITY: PluginActor unconditionally satisfies `has_permission`. It is
    the in-process trust principal for kernel-internal and plugin-internal
    code paths (outbox dispatch, scheduled jobs, system tasks). NEVER
    construct a PluginActor from user-controllable input — doing so bypasses
    every `@requires` check downstream. Use entity-actors for any caller whose
    identity flows from an external request.
    """

    actor_kind: ClassVar[str] = "plugin"
    alias: str

    async def has_permission(self, uow: UnitOfWork, permission: str) -> bool:
        return True  # plugin code is trusted; permission checks gate external callers
