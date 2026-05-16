"""Auth plugin actions."""

from hearth_auth.actions.api_key import (
    AuthenticateApiKey,
    CreateApiKey,
    RevokeApiKey,
)
from hearth_auth.actions.permission import (
    AssignPermissionToRole,
    CreatePermission,
    RevokePermissionFromRole,
)
from hearth_auth.actions.role import (
    AssignRoleToUser,
    CreateRole,
    RevokeRoleFromUser,
)
from hearth_auth.actions.user import (
    ChangePassword,
    CreateUser,
    DisableUser,
    EnableUser,
)
from hearth_auth.actions.user_auth import AuthenticateUser

__all__ = [
    "AssignPermissionToRole",
    "AssignRoleToUser",
    "AuthenticateApiKey",
    "AuthenticateUser",
    "ChangePassword",
    "CreateApiKey",
    "CreatePermission",
    "CreateRole",
    "CreateUser",
    "DisableUser",
    "EnableUser",
    "RevokeApiKey",
    "RevokePermissionFromRole",
    "RevokeRoleFromUser",
]
