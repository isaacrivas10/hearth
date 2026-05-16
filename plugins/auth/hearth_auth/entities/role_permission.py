"""RolePermission — many-to-many join between Roles and Permissions."""

from __future__ import annotations

from hearth import EntityId, ForeignKey, References
from hearth_auth._base import Entity
from hearth_auth.entities.permission import Permission
from hearth_auth.entities.role import Role


class RolePermission(Entity):
    role_id: EntityId = ForeignKey()
    role: Role = References(Role)
    permission_id: EntityId = ForeignKey()
    permission: Permission = References(Permission)
