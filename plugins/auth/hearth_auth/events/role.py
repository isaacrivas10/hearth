"""Events emitted by role-lifecycle actions."""

from __future__ import annotations

from hearth import EntityId
from hearth_auth._base import Event


class RoleCreated(Event):
    role_id: EntityId
    name: str


class RoleAssignedToUser(Event):
    user_id: EntityId
    role_id: EntityId
    granted_by_id: EntityId | None


class RoleRevokedFromUser(Event):
    user_id: EntityId
    role_id: EntityId
