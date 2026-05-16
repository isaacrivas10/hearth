"""Auth plugin entity types."""

from hearth_auth.entities.api_key import ApiKey
from hearth_auth.entities.api_key_permission import ApiKeyPermission
from hearth_auth.entities.permission import Permission
from hearth_auth.entities.role import Role
from hearth_auth.entities.role_permission import RolePermission
from hearth_auth.entities.user import User
from hearth_auth.entities.user_role import UserRole

__all__ = [
    "ApiKey",
    "ApiKeyPermission",
    "Permission",
    "Role",
    "RolePermission",
    "User",
    "UserRole",
]
