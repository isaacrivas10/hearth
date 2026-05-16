"""Events emitted by permission-lifecycle actions."""

from __future__ import annotations

from hearth import EntityId
from hearth_auth._base import Event
from hearth_auth.values import PermissionName


class PermissionCreated(Event):
    permission_id: EntityId
    name: PermissionName


class PermissionAssignedToRole(Event):
    role_id: EntityId
    permission_id: EntityId


class PermissionRevokedFromRole(Event):
    role_id: EntityId
    permission_id: EntityId
