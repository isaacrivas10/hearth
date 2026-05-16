"""Auth plugin events."""

from hearth_auth.events.api_key import (
    ApiKeyAuthenticated,
    ApiKeyCreated,
    ApiKeyRevoked,
)
from hearth_auth.events.permission import (
    PermissionAssignedToRole,
    PermissionCreated,
    PermissionRevokedFromRole,
)
from hearth_auth.events.role import (
    RoleAssignedToUser,
    RoleCreated,
    RoleRevokedFromUser,
)
from hearth_auth.events.user import (
    PasswordChanged,
    UserAuthenticated,
    UserCreated,
    UserDisabled,
    UserEnabled,
)

__all__ = [
    "ApiKeyAuthenticated",
    "ApiKeyCreated",
    "ApiKeyRevoked",
    "PasswordChanged",
    "PermissionAssignedToRole",
    "PermissionCreated",
    "PermissionRevokedFromRole",
    "RoleAssignedToUser",
    "RoleCreated",
    "RoleRevokedFromUser",
    "UserAuthenticated",
    "UserCreated",
    "UserDisabled",
    "UserEnabled",
]
