"""UserRole — many-to-many join between Users and Roles."""

from __future__ import annotations

from datetime import datetime

from hearth import EntityId, Field, ForeignKey, References
from hearth_auth._base import Entity
from hearth_auth.entities.role import Role
from hearth_auth.entities.user import User


class UserRole(Entity):
    user_id: EntityId = ForeignKey()
    user: User = References(User)
    role_id: EntityId = ForeignKey()
    role: Role = References(Role)
    granted_at: datetime = Field(auto_now_add=True)
    granted_by_id: EntityId | None = None
    granted_by: User | None = References(User)
