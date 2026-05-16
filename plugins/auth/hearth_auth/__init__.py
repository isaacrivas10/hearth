"""hearth_auth: users, roles, permissions, and api keys for Hearth."""

from hearth_auth.entities import (
    ApiKey,
    ApiKeyPermission,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from hearth_auth.helpers import (
    AuthenticationFailed,
    PermissionDenied,
    PermissionNotFound,
    permission_grant_exists,
    require_permission,
    requires,
)
from hearth_auth.values import HashedSecret, PermissionName

__all__ = [
    "ApiKey",
    "ApiKeyPermission",
    "AuthenticationFailed",
    "HashedSecret",
    "Permission",
    "PermissionDenied",
    "PermissionName",
    "PermissionNotFound",
    "Role",
    "RolePermission",
    "User",
    "UserRole",
    "permission_grant_exists",
    "require_permission",
    "requires",
]
