"""Permission entity — one row per (resource, action) tuple. Bound to Roles
via RolePermission, to ApiKeys via ApiKeyPermission."""

from __future__ import annotations

from hearth import Unique
from hearth_auth._base import Entity
from hearth_auth.values import PermissionName


class Permission(Entity):
    name: PermissionName
    description: str | None = None

    # (resource, action) must be globally unique. Without this, the bootstrap
    # guard and CreatePermission can race to insert duplicate wildcards, and
    # role/permission resolution would have to deduplicate at read time.
    __hearth_table_args__ = (Unique("name_resource", "name_action"),)
